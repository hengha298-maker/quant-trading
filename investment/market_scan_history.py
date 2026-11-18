"""At most three isolated stock history reviews for the discovery queue.

Reuses the existing 600-session benchmark/holding caches and Decimal factors.
No portfolio writes, trading admission, broad history crawl, or new thresholds.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, localcontext
from pathlib import Path
from urllib.parse import urlencode
import hashlib,json,re,uuid
from .holdings import _safe_path,_canonical
from .market import _attempt,_daily_parser,TENCENT_URL,BJ,xshg_sessions,strict_json
from .radar_market import _cached,catalog
from .radar import features,score,_values
from .research_state import RULES,TRADE_GAPS
from .market_scan import _load as _discovery_collection
from goose_research.market import validate_dataset,calendar_supported_through

def _clock(now=None):
 value=now or datetime.now(timezone.utc)
 if value.tzinfo is None:raise ValueError('DISCOVERY_HISTORY_AWARE_CLOCK_REQUIRED')
 return value.astimezone(timezone.utc)
def _bytes(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def _fmt(value):return format(value,'.6f') if isinstance(value,D) else value

def parse_history(raw,spec,start,end,basis):
 text=raw.decode('utf-8-sig').strip();text=text[6:].rstrip(';') if text.startswith('goose=') else text
 doc=strict_json(text.encode());provider=spec['symbol'][-2:].lower()+spec['symbol'][:6]
 data=(doc.get('data') or {}).get(provider) or {};qt=(data.get('qt') or {}).get(provider)
 if not isinstance(qt,list) or len(qt)<5 or qt[2]!=spec['symbol'][:6] or qt[1]!=spec['name']:raise ValueError('DISCOVERY_TENCENT_QUOTE_IDENTITY_MISMATCH')
 return _daily_parser(raw,spec['symbol'],start,end,basis)

def _original_checks(f,s):
 # Exact, unrounded existing stock checks in radar.build. No peer rank or
 # trading eligibility is asserted from this isolated three-stock sample.
 checks={'score':s['value']>=65,'trend':f['close']>f['ma20']>f['ma60'],
  'momentum':f['momentum20']>0 and f['relative60']>=0,
  'risk':f['drawdown126']<=D('.35') and f['vol60']<=D('.50'),
  'liquidity':f['estimatedTurnover20']>=D(100000000),
  'basis':not f['adjustmentDirectionConflict'],'correlation':bool(f['correlations']) and not f['missingHoldingCorrelations']}
 return [{'id':key,'label':label,'condition':condition,'rejection':rejection,'status':('passed' if checks[key] else 'failed') if key in checks else 'unknown'} for key,label,rejection,condition in RULES]

def review(history,benchmark,held,spec,*,quote_reference=None):
 """Pure review. All supplied prices remain separated by adjustment basis."""
 with localcontext() as ctx:
  ctx.prec=45;f=features(history,benchmark,held);sc=score(f,spec);checks=_original_checks(f,sc)
 rows=history['raw']['bars'];adjusted=history['adjusted']['bars'];through=rows[-1]['date']
 near=next(p['points'] for p in sc['parts'] if p['factor']=='突破位置')
 breakout=bool(f['close']>f['prior20High'])
 pullback_observed=bool(D(adjusted[-1]['close'])<D(adjusted[-2]['close']) and f['close']>f['ma20']>f['ma60'])
 failed=[r for r in checks if r['status']=='failed'];unknown=[r for r in checks if r['status']=='unknown']
 volume_average=sum(D(r['volume']) for r in rows[-20:-1])/19
 trend=next(r for r in checks if r['id']=='trend');momentum=next(r for r in checks if r['id']=='momentum')
 next_condition=('等待下一完整日的同口径收盘>MA20>MA60，并核对20日动量为正、60日相对强弱不为负；当前MA与高点只是有日期的研究参照，不是买入价。' if trend['status']=='failed' or momentum['status']=='failed' else '下一完整日继续核对：'+failed[0]['condition'] if failed else '下一完整日维持同口径收盘>MA20>MA60、相对强弱和流动性，并完成剩余准入核验。')
 return {'status':'validated-isolated-price-research','label':'隔离日线证据已核验，仍未通过完整交易准入','through':through,'source':'tencent-public-daily','fetchedAt':history['raw']['fetchedAt'],'providerIdentityEchoVerified':True,
  'ruleSource':'investment/radar.py features/score/build；未修改原规则','ruleSourceSha256':hashlib.sha256((Path(__file__).parent/'radar.py').read_bytes()).hexdigest(),
  'momentum5Pct':_fmt((D(adjusted[-1]['close'])/D(adjusted[-6]['close'])-1)*100),'momentum10Pct':_fmt((D(adjusted[-1]['close'])/D(adjusted[-11]['close'])-1)*100),'momentum20Pct':_fmt(f['momentum20']*100),'momentumBasis':'provider-qfq-complete-sessions-not-execution-price',
  'historyCount':len(rows),'rawLastClose':_fmt(f['rawClose']),'rawMA20':_fmt(f['rawMa20']),'rawMA60':_fmt(f['rawMa60']),'providerQfqLastClose':_fmt(f['close']),'providerQfqMA20':_fmt(f['ma20']),'providerQfqMA60':_fmt(f['ma60']),
  'volumeShares':rows[-1]['volume'],'previous19MeanVolumeShares':_fmt(volume_average),'volumeRatio':_fmt(f['volumeRatio']) if f['volumeRatio'] is not None else None,
  'relative60Pct':_fmt(f['relative60']*100),'benchmarkSymbol':benchmark['symbol'],'benchmarkThrough':benchmark['through'],'benchmarkMeaning':'沪深300ETF前复权价格代理，非指数总回报',
  'researchScore':sc,'features':_values(f),'originalRuleChecks':checks,'originalRejections':[r['rejection'] for r in failed],
  'breakout':{'confirmedOnCompleteSession':breakout,'nearOriginalBand':near==5,'closeQfq':str(f['close']),'prior20HighQfq':str(f['prior20High']),'windowExcludesSignalDay':True,'through':through,'meaning':'已越过前20日高点' if breakout else '未形成原规则突破；临近也不等于触发'},
  'pullback':{'observedDayDeclineAboveMA20MA60':pullback_observed,'strategyEntryConfirmed':False,'meaning':'仅观察到完整日回落且仍在MA20/60之上；原项目没有已验证回调买入规则，不虚构触发价。' if pullback_observed else '未观察到“完整日下跌且收盘>MA20>MA60”；未建立新的回调策略。'},
  'nextCondition':next_condition,'ifSatisfied':'只增加研究依据，仍须完整准入、资金和风险核验；不等于可以买入。',
  'invalidation':'完整日跌回MA20、60日相对强弱转负、原始价与复权趋势冲突或交易日/单位/来源异常时撤销观察。',
  'tradeAdmitted':False,'tradeCandidate':False,'entryRange':None,'stopPrice':None,'targetRange':None,'riskRewardRatio':None,
  'admissionGaps':[*TRADE_GAPS,*[r['condition'] for r in unknown],'这里只审查最多3只，不满足原至少4只股票同类比较证明。','证券交易制度、实际盘中报价、估值和个人可用资金未完整核验。'],
  'quoteReference':quote_reference,'backtest':{'status':'not-run-isolated-review','enoughForTrading':False,'reason':'本次只核验历史条件；未将三只事后高成交额样本当作无偏策略成绩。'},
  'correlationThrough':{symbol:history['through'] if h and h['through']>=history['through'] else h['through'] if h else None for symbol,h in held.items()},
  'limitations':['原始价用于价格单位和成交口径；前复权只用于既有研究指标，二者不混作成交价格。','现有缓存基准截止日期决定共同研究截止日，可能早于最新广域快照。','前复权可事后修订；未核验逐笔分红、税、拆分、退市或历史点时成员，无前瞻盈利结论。']}

def _cache(root):
 base=_safe_path(Path(root)/'investment/work/market-discovery/history',missing=True);base.mkdir(parents=True,exist_ok=True);return base

def _cached_at(root,symbol,through=None):
 folder=Path(root)/'investment/work/radar/market'
 if through is None:return _cached(folder,symbol)
 for p in sorted(folder.glob(symbol+'-*.json'),reverse=True):
  try:
   doc=json.loads(_safe_path(p,file=True).read_text(encoding='utf8'))
   if doc.get('through')!=through or doc.get('symbol')!=symbol:continue
   if doc.get('sha256')!=hashlib.sha256(_canonical({k:v for k,v in doc.items() if k!='sha256'})).hexdigest():continue
   validate_dataset(doc['raw'])
   if doc['raw']['quality']['status']!='validated' or doc['adjusted'].get('adjustment')!='qfq' or doc['adjusted'].get('symbol')!=symbol:continue
   if [r['date'] for r in doc['adjusted']['bars']]!=doc['raw']['calendar']['sessions']:continue
   return doc
  except (ValueError,OSError,KeyError,TypeError):continue
 return None

def load_cached(root,discovery,*,now=None):
 clock=_clock(now);base=_cache(root);reviews={}
 for item in [i for i in discovery.get('items',[]) if i.get('kind')=='stock'][:3]:
  symbol=item['symbol'];files=sorted(base.glob(symbol+'-*.json'))
  if not files:continue
  try:
   p=_safe_path(files[-1],file=True)
   if p.stat().st_size>1024*1024:continue
   data=json.loads(p.read_text(encoding='utf8'));digest=data.pop('sha256')
   if hashlib.sha256(_bytes(data)).hexdigest()!=digest or data.get('synthetic') or data['symbol']!=symbol:continue
   result=data['review'];result['viewedAt']=clock.isoformat();result['cacheReused']=True
   result['isHistoricalReference']=result.get('through')!=discovery.get('asOf')
   reviews[symbol]=result
  except (ValueError,OSError,KeyError,TypeError):continue
 return {'format':'goose-discovery-history-review','version':1,'generatedAt':clock.isoformat(),'sampleLimit':3,'reviews':reviews,'sampleCount':len(reviews),'tradeCandidateCount':0,'cachedOnly':True}

def generate(root,discovery,*,deadline=None,now=None,attempt=None,recorded_run=None):
 clock=_clock(now);deadline=deadline or clock+timedelta(seconds=35);base=_cache(root)
 specs=[];seen=set()
 for item in discovery.get('items',[]):
  symbol=item.get('symbol','')
  if item.get('kind')!='stock' or symbol in seen or not re.fullmatch(r'(?:[036]\d{5})\.(?:SH|SZ)',symbol):continue
  seen.add(symbol);specs.append({'symbol':symbol,'name':item['name'],'kind':'stock','category':item.get('industry') or '广域发现','tick':'0.01'})
  if len(specs)==3:break
 # Board price limits are unreviewed; no backtest/order/limit rule is supplied.
 target_through=None
 if recorded_run is not None:
  if attempt is not None or not re.fullmatch(r'[a-f0-9]{32}',recorded_run):raise ValueError('DISCOVERY_RECORDED_RUN_INVALID')
  source_summary=json.loads(_safe_path(base/'runs'/recorded_run/'summary.json',file=True).read_text(encoding='utf8'))
  target_through=source_summary.get('benchmarkThrough')
  if not isinstance(target_through,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',target_through):raise ValueError('DISCOVERY_RECORDED_BENCHMARK_DATE_MISSING')
 cat=catalog();benchmark=_cached_at(root,cat['benchmark'],target_through)
 if not benchmark:return {'format':'goose-discovery-history-review','version':1,'generatedAt':clock.isoformat(),'sampleCount':0,'sampleLimit':3,'reviews':{},'codes':['DISCOVERY_BENCHMARK_CACHE_MISSING'],'tradeCandidateCount':0}
 dates=benchmark['raw']['calendar']['sessions'];start,end=dates[0],dates[-1]
 held={}
 for symbol in ('600900.SH','515450.SH'):
  try:h=_cached_at(root,symbol,end)
  except (ValueError,OSError,KeyError):h=None
  held[symbol]=h if h and h['through']==end else None
 run_id=uuid.uuid4().hex;run=base/'runs'/run_id;run.mkdir(parents=True,exist_ok=False)
 request=attempt or _attempt;observations=[];reviews={};full=None
 if recorded_run is not None:
  if attempt is not None or not re.fullmatch(r'[a-f0-9]{32}',recorded_run):raise ValueError('DISCOVERY_RECORDED_RUN_INVALID')
  original=_safe_path(base/'runs'/recorded_run)
  originals=json.loads(_safe_path(original/'observations.json',file=True).read_text(encoding='utf8'))
  def recorded(source,symbol,url,parser,deadline):
   basis='qfq' if source.endswith('-qfq') else 'none'
   obs=next((o for o in originals if o.get('source')==source and o.get('symbol')==symbol and o.get('basis')==basis),None)
   if not obs or obs.get('httpStatus')!=200 or obs.get('endpoint')!=TENCENT_URL or not re.fullmatch(r'\d{6}\.(?:SH|SZ)-(none|qfq)\.raw',obs.get('rawFile','')):raise ValueError('DISCOVERY_RECORDED_OBSERVATION_INVALID')
   raw=_safe_path(original/obs['rawFile'],file=True).read_bytes()
   if hashlib.sha256(raw).hexdigest()!=obs.get('rawSha256'):raise ValueError('DISCOVERY_RECORDED_HASH_MISMATCH')
   value=dict(obs);value['revalidatedRecordedRaw']=True
   try:value['value']=parser(raw)
   except ValueError as exc:value['value']=None;value['errors']=[str(exc) if re.fullmatch('[A-Z0-9_]{1,120}',str(exc)) else 'DISCOVERY_RECORDED_FIELDS_REJECTED']
   return value,raw
  request=recorded
 try:full=_discovery_collection(root,clock)
 except (ValueError,OSError,KeyError):pass
 reference={i['symbol']:i for i in (full or {}).get('items',[]) if i.get('kind')=='stock'}
 def fetch(job):
  spec,basis=job;symbol=spec['symbol'];provider=symbol[-2:].lower()+symbol[:6]
  url=TENCENT_URL+'?'+urlencode({'_var':'goose','param':f'{provider},day,{start},{end},640,{"qfq" if basis=="qfq" else ""}'})
  obs,raw=request('tencent-discovery-history-'+basis,symbol,url,lambda raw:parse_history(raw,spec,start,end,basis),deadline=deadline)
  obs=dict(obs);rows=obs.pop('value',None);obs['basis']=basis
  if raw is not None:obs['rawFile']=symbol+'-'+basis+'.raw';(run/obs['rawFile']).write_bytes(raw)
  return symbol,basis,obs,rows
 results={s['symbol']:{} for s in specs}
 with ThreadPoolExecutor(max_workers=3) as pool:
  for symbol,basis,obs,rows in pool.map(fetch,[(s,b) for s in specs for b in ('none','qfq')]):
   observations.append(obs);results[symbol][basis]=(rows,obs)
 for spec in specs:
  symbol=spec['symbol'];qref=None;anomalies=[]
  try:
   raw,rawobs=results[symbol]['none'];adjusted,adjobs=results[symbol]['qfq']
   if raw is None or adjusted is None:raise ValueError('DISCOVERY_HISTORY_SOURCE_OR_BASIS_FAILED')
   if [r['date'] for r in raw]!=dates or [r['date'] for r in adjusted]!=dates:raise ValueError('DISCOVERY_HISTORY_CALENDAR_GAP')
   if any(D(r[k])%D('.01') for r in raw for k in ('open','high','low','close')):raise ValueError('DISCOVERY_HISTORY_STOCK_TICK_INVALID')
   if [r['volume'] for r in raw]!=[r['volume'] for r in adjusted]:raise ValueError('DISCOVERY_HISTORY_BASIS_VOLUME_CONFLICT')
   if any(r['volume']<=0 for r in raw[-20:]):raise ValueError('DISCOVERY_HISTORY_TRADING_STATUS_UNVERIFIED')
   anomalies=[{'date':raw[i]['date'],'previousClose':raw[i-1]['close'],'close':raw[i]['close'],'changePct':_fmt((D(raw[i]['close'])/D(raw[i-1]['close'])-1)*100)} for i in range(1,len(raw)) if abs(D(raw[i]['close'])/D(raw[i-1]['close'])-1)>D('.25')]
   if anomalies:raise ValueError('DISCOVERY_STOCK_ACTION_OR_SCALE_REQUIRES_REVIEW')
   ref=reference.get(symbol) or {};source_day=ref.get('dataDate')
   expected=ref.get('price') if source_day==end else ref.get('previousClose') if source_day and source_day>end else None
   if expected is None or not ref.get('fieldValidated'):raise ValueError('DISCOVERY_CROSS_SOURCE_PRICE_REFERENCE_MISSING')
   if abs(D(raw[-1]['close'])-D(expected))>D('.01'):raise ValueError('DISCOVERY_CROSS_SOURCE_PRICE_SCALE_CONFLICT')
   qref={'source':ref['source'],'sourceTimestamp':ref['sourceTimestamp'],'dataDate':source_day,'value':expected,'meaning':'同日价格或下一交易日昨收与历史末日收盘核对；不是完整独立行情认证'}
   dataset={'format':'goose-market-dataset','version':1,'symbol':symbol,'kind':'stock','source':'tencent','sourceUrl':TENCENT_URL,'adjustment':'none','currency':'CNY','priceUnit':'CNY-per-share','fetchedAt':rawobs['receivedAt'],'calendar':{'name':'XSHG','sessions':dates,'supportedThrough':calendar_supported_through()},'bars':raw,
    'quality':{'status':'synthetic' if attempt else 'validated','crossValidated':False,'unitVerified':True,'corporateActionsVerified':False,'unitEvidence':'Reviewed public daily adapter lots-to-shares semantics, 0.01 raw tick and same/next-session delayed provider price scale consistency; not independent exchange feed','warnings':['DISCOVERY_CURRENT_SURVIVORS_NOT_POINT_IN_TIME_UNIVERSE','PROVIDER_QFQ_REVISION_AND_DIVIDENDS_UNVERIFIED']}}
   dataset=validate_dataset(dataset)
   h={'symbol':symbol,'through':end,'raw':dataset,'adjusted':{'symbol':symbol,'adjustment':'qfq','bars':adjusted,'source':'tencent','fetchedAt':adjobs['receivedAt'],'totalReturnVerified':False}}
   if attempt:raise ValueError('DISCOVERY_SYNTHETIC_HISTORY_NOT_PRODUCTION')
   result=review(h,benchmark,held,spec,quote_reference=qref)
  except (ValueError,OSError,KeyError,TypeError,ArithmeticError) as exc:
   code=str(exc) if re.fullmatch(r'[A-Z0-9_]{1,120}',str(exc)) else 'DISCOVERY_HISTORY_REVIEW_FAILED'
   receipts=[o.get('receivedAt') for o in observations if o.get('symbol')==symbol and o.get('receivedAt')]
   result={'status':'insufficient','label':'日线/口径未完成核验，不输出方向','through':end,'fetchedAt':max(receipts) if receipts else None,'reviewedAt':clock.isoformat(),'codes':[code],'rawHistoryAnomalies':anomalies,'observations':[{'source':o['source'],'basis':o['basis'],'httpStatus':o.get('httpStatus'),'errors':o.get('errors',[])} for o in observations if o.get('symbol')==symbol],
    'nextCondition':'先核对异常日的企业行为、原始/复权序列及价格尺度；隔离未解除前不判断突破或回调。' if anomalies else '先可靠取得本标的none和qfq完整日线、价格单位与共同基准；缺口未解除前不判断突破或回调。','tradeAdmitted':False,'tradeCandidate':False,'entryRange':None,'stopPrice':None,'targetRange':None,'admissionGaps':TRADE_GAPS}
  record={'format':'goose-discovery-history-item','version':1,'symbol':symbol,'name':spec['name'],'generatedAt':clock.isoformat(),'review':result,'synthetic':attempt is not None}
  record['sha256']=hashlib.sha256(_bytes(record)).hexdigest()
  target=base/(symbol+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json');temporary=target.with_suffix('.tmp');temporary.write_bytes(_bytes(record));temporary.replace(target);reviews[symbol]=result
 (run/'observations.json').write_bytes(_bytes(observations))
 summary={'format':'goose-discovery-history-review','version':1,'generatedAt':clock.isoformat(),'sampleLimit':3,'sampleCount':len(reviews),'benchmarkThrough':end,'reviews':reviews,'tradeCandidateCount':0,'synthetic':attempt is not None}
 (run/'summary.json').write_bytes(_bytes(summary));return summary
