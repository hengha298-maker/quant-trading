"""Broad public-market discovery, separate from the reviewed strategy pool.

This is a dated provider snapshot and a research queue, never trading admission.
Pagination completeness is checked against provider totals, not assumed to be an
exchange-certified historical universe. No per-security history is downloaded.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
import threading
from urllib.parse import urlencode
import hashlib, json, re, uuid

from .holdings import _safe_path
from .market import _attempt, BJ, xshg_sessions

ENDPOINT = 'https://push2delay.eastmoney.com/api/qt/clist/get'
SOURCE = 'eastmoney-delayed-market-discovery'
PAGE_SIZE = 100  # Actual service cap verified with a 1,000-row probe.
MAX_PAGES = 100
SOURCES = {
 'stock': {'filter':'m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23','label':'供应商沪深A股列表（另行排除CDR）'},
 'etf': {'filter':'b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827','label':'供应商沪深ETF列表，含跨境/债券等类型'},
 'industry': {'filter':'m:90 t:2','label':'供应商行业板块（层级可能重叠）'},
}
FIELDS='f2,f3,f5,f6,f10,f12,f13,f14,f15,f16,f17,f18,f24,f62,f100,f104,f105,f124,f297'
REFERENCE_SOURCES = [
 'https://raw.githubusercontent.com/akfamily/akshare/main/akshare/stock/quote/stock_hist_em.py',
 'https://raw.githubusercontent.com/akfamily/akshare/main/akshare/fund/fund_etf_em.py',
 'https://akshare.akfamily.xyz/data/stock/stock_quote.html',
 'https://akshare.akfamily.xyz/data/fund/fund_public.html',
]
LIMITS = [
 '广域发现与原16只严格研究池分开；本模块不授予交易准入，不改变原策略阈值。',
 '使用供应商延迟公开快照；源时间是提供方更新时间，不是认证成交时刻，不能用于盘中执行。',
 '全量只指本次供应商列表分页完整；未逐项核对交易所正式证券名录，不是无幸存者偏差的历史全市场。',
 '当日涨跌、供应商60日涨幅与资金分类只作研究线索；MA、突破、回调及可执行价需要同口径完整日历史另验。',
 '主力净流入是供应商按订单类别推算的净额，未经独立核验，不等于账户现金流、真实投资者资金或成交量净值。',
]

def _clock(now):
 value=now or datetime.now(timezone.utc)
 if not isinstance(value,datetime) or value.tzinfo is None:raise ValueError('SCAN_AWARE_CLOCK_REQUIRED')
 return value.astimezone(timezone.utc)

def _bytes(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def _json(raw):
 if len(raw)>2*1024*1024:raise ValueError('SCAN_PAGE_TOO_LARGE')
 def unique(pairs):
  result={}
  for k,v in pairs:
   if k in result or k in {'__proto__','constructor','prototype'}:raise ValueError('SCAN_DUPLICATE_JSON_KEY')
   result[k]=v
  return result
 try:return json.loads(raw.decode('utf-8-sig'),parse_float=Decimal,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('SCAN_NONFINITE_NUMBER')),object_pairs_hook=unique)
 except (UnicodeError,json.JSONDecodeError,RecursionError):raise ValueError('SCAN_JSON_INVALID')

def _number(value,*,positive=False,nonnegative=False):
 if value in (None,'','-'):return None
 if type(value) not in (str,int,Decimal):raise ValueError('SCAN_NUMERIC_FIELD_INVALID')
 try:result=Decimal(str(value))
 except InvalidOperation:raise ValueError('SCAN_NUMERIC_FIELD_INVALID')
 if not result.is_finite() or abs(result)>Decimal('1e18'):raise ValueError('SCAN_NUMERIC_FIELD_INVALID')
 if positive and result<=0 or nonnegative and result<0:raise ValueError('SCAN_NONPOSITIVE_FIELD')
 return result
def _text(value):return format(value,'f') if isinstance(value,Decimal) else value

def parse_page(raw,kind):
 if kind not in SOURCES:raise ValueError('SCAN_KIND_INVALID')
 doc=_json(raw);data=doc.get('data') if isinstance(doc,dict) else None
 if not isinstance(doc,dict) or type(doc.get('rc')) is not int or doc['rc']!=0 or not isinstance(data,dict):raise ValueError('SCAN_RETURN_INVALID')
 total=data.get('total');rows=data.get('diff')
 if type(total) is not int or not 0<total<=10000 or not isinstance(rows,list) or not 1<=len(rows)<=PAGE_SIZE:raise ValueError('SCAN_PAGE_COUNTS_INVALID')
 items=[];errors=[];seen=set()
 for row in rows:
  if not isinstance(row,dict):raise ValueError('SCAN_ROW_INVALID')
  code=row.get('f12');market=row.get('f13');name=row.get('f14')
  if not isinstance(code,str) or not isinstance(name,str) or not 1<=len(name)<=80 or re.search(r'[\x00-\x1f<>]',name):raise ValueError('SCAN_IDENTITY_INVALID')
  if kind=='industry':
   if not re.fullmatch(r'BK\d{4}',code) or market!=90:raise ValueError('SCAN_BOARD_IDENTITY_INVALID')
   symbol=code
  else:
   if not re.fullmatch(r'\d{6}',code) or type(market) is not int or market not in {0,1}:raise ValueError('SCAN_SECURITY_IDENTITY_INVALID')
   symbol=code+('.SH' if market==1 else '.SZ')
  if symbol in seen:raise ValueError('SCAN_DUPLICATE_SYMBOL')
  seen.add(symbol)
  item={'symbol':symbol,'name':name,'kind':kind,'source':SOURCE,'price':None,'previousClose':None,'changePct':None,'volumeLots':None,'amountCNY':None,'sourceTimestamp':None,'dataDate':None,
   'adjustment':'none','currency':'CNY','priceUnit':'provider-index-points' if kind=='industry' else 'CNY-per-share','intradaySignalAllowed':False,'independentFeedVerified':False,'codes':[]}
  try:
   supported=(kind=='industry' or kind=='stock' and ((market==1 and code.startswith(('600','601','603','605','688'))) or (market==0 and code.startswith(('000','001','002','003','300','301')))) or kind=='etf' and ((market==1 and code.startswith(('5',))) or (market==0 and code.startswith(('15','16')))))
   if not supported:item['codes'].append('IDENTITY_OR_SECURITY_TYPE_REQUIRES_EXCHANGE_REVIEW')
   if kind=='stock' and (re.search(r'ST|退',name,re.I) or re.match('[NC]',name)):item['codes'].append('RISK_WARNING_OR_NEW_LISTING_REQUIRES_REVIEW')
   stamp=row.get('f124');date=row.get('f297')
   if type(stamp) is not int or not 946684800<=stamp<=4102444800:raise ValueError('SCAN_SOURCE_TIME_MISSING')
   source=datetime.fromtimestamp(stamp,timezone.utc).astimezone(BJ)
   day=datetime.strptime(str(date),'%Y%m%d').date().isoformat() if type(date) is int else None
   if day!=source.date().isoformat():raise ValueError('SCAN_SOURCE_DATE_CONFLICT')
   item.update(sourceTimestamp=source.isoformat(),dataDate=day)
   # A provider zero price is unavailable/status evidence, never a zero value.
   price,prev,high,low,opening=[None if row.get(k) in (0,'0','-','',None) else _number(row.get(k),positive=True) for k in ['f2','f18','f15','f16','f17']]
   if any(x is None for x in [price,prev,high,low,opening]):item['codes'].append('PRICE_OR_TRADING_STATUS_MISSING_NOT_ZERO')
   else:
    if not low<=min(price,opening)<=max(price,opening)<=high:raise ValueError('SCAN_OHLC_INVALID')
    tick=Decimal('.001') if kind=='etf' else Decimal('.01')
    if kind!='industry' and any(v%tick for v in [price,prev,high,low,opening]):raise ValueError('SCAN_PRICE_TICK_INVALID')
   change=_number(row.get('f3'));volume=_number(row.get('f5'),nonnegative=True);amount=_number(row.get('f6'),nonnegative=True)
   if price is not None and prev is not None and change is not None and abs((price/prev-1)*100-change)>Decimal('.02'):raise ValueError('SCAN_PERCENT_SCALE_CONFLICT')
   if volume is not None and volume!=volume.to_integral_value():raise ValueError('SCAN_VOLUME_UNIT_INVALID')
   item.update(price=_text(price),previousClose=_text(prev),high=_text(high),low=_text(low),open=_text(opening),changePct=_text(change),volumeLots=int(volume) if volume is not None else None,amountCNY=_text(amount),sourceTimestamp=source.isoformat(),dataDate=day,
     providerVolumeRatio=_text(_number(row.get('f10'),nonnegative=True)),providerChange60Pct=_text(_number(row.get('f24'))),providerMainNetInflowCNY=_text(_number(row.get('f62'))),industry=row.get('f100') if isinstance(row.get('f100'),str) and row['f100']!='-' else None,
     providerFlowVerified=False,historyValidated=False)
   if not volume or not amount:item['codes'].append('INACTIVE_OR_VOLUME_MISSING_NOT_SUSPENSION_CERTIFICATION')
  except (ValueError,TypeError,OverflowError) as exc:
   item['codes'].append(str(exc) if re.fullmatch('[A-Z0-9_]{1,100}',str(exc)) else 'SCAN_FIELDS_INVALID')
  item['fieldValidated']=not item['codes'];items.append(item)
  if item['codes']:errors.append({'symbol':symbol,'codes':item['codes']})
 return {'total':total,'items':items,'rowErrors':errors}

def _url(kind,page):
 return ENDPOINT+'?'+urlencode({'pn':page,'pz':PAGE_SIZE,'po':1,'np':1,'ut':'bd1d9ddb04089700cf9c27f6f7426281','fltt':2,'invt':2,'fid':'f12','fs':SOURCES[kind]['filter'],'fields':FIELDS})

def _assess(item,observed,now):
 value=dict(item);codes=list(value['codes']);source=None
 try:
  source=datetime.fromisoformat(value['sourceTimestamp']);day=source.astimezone(BJ).date().isoformat()
  if day not in xshg_sessions(day,day):codes.append('SCAN_NONTRADING_SOURCE_DATE')
  if source>now+timedelta(seconds=5):codes.append('SCAN_SOURCE_TIME_IN_FUTURE')
 except (ValueError,TypeError):codes.append('SCAN_CALENDAR_OR_TIME_UNVERIFIED')
 value['codes']=list(dict.fromkeys(codes));value['fieldValidated']=not codes
 value['fetchedAt']=observed.get('receivedAt');value['rawSha256']=observed.get('rawSha256')
 value['sourceAgeSeconds']=round((now-source).total_seconds(),3) if source is not None else None
 value['freshness']='delayed-research-snapshot' if source and source.astimezone(BJ).date()==now.astimezone(BJ).date() else 'historical-or-invalid'
 value['verification']='provider-fields-checked-delayed-not-trading' if value['fieldValidated'] else 'blocked'
 return value

def summarize(collection,*,now=None):
 clock=_clock(now);parts=collection['groups'];items=collection['items'];today=clock.astimezone(BJ).date().isoformat()
 valid=[i for i in items if i.get('fieldValidated') and i.get('dataDate')==collection.get('asOf')]
 stocks=[i for i in valid if i['kind']=='stock'];funds=[i for i in valid if i['kind']=='etf'];boards=[i for i in valid if i['kind']=='industry']
 def stats(rows):
  changes=sorted(Decimal(i['changePct']) for i in rows if i.get('changePct') is not None)
  n=len(changes);mid=(changes[(n-1)//2]+changes[n//2])/2 if n else None
  return {'count':len(rows),'rising':sum(c>0 for c in changes),'falling':sum(c<0 for c in changes),'unchanged':sum(c==0 for c in changes),'medianChangePct':_text(mid),'amountCNY':_text(sum((Decimal(i['amountCNY']) for i in rows if i.get('amountCNY') is not None),Decimal(0))) if rows else None}
 stock_stats=stats(stocks);median=Decimal(stock_stats['medianChangePct']) if stock_stats['medianChangePct'] is not None else None
 candidates=[]
 for kind,rows in [('stock',stocks),('etf',funds)]:
  # Queue by published liquidity, not a newly invented buy-score threshold.
  # Daily relative strength describes this snapshot, not 60-day strategy RS.
  ranked=sorted(rows,key=lambda i:(-Decimal(i.get('amountCNY') or '0'),i['symbol']))
  for i in ranked[:5]:
   delta=Decimal(i['changePct'])-median if i.get('changePct') is not None and median is not None and kind=='stock' else None
   stamp=datetime.fromisoformat(i['sourceTimestamp']);age=round((clock-stamp).total_seconds(),3)
   historical=collection.get('asOf')!=today
   freshness='historical-delayed-research' if historical else 'delayed-closing-research' if clock.astimezone(BJ).time().replace(tzinfo=None)>=time(15,30) else 'delayed-research-not-execution'
   candidates.append({k:i.get(k) for k in ['symbol','name','kind','industry','price','changePct','volumeLots','providerVolumeRatio','amountCNY','source','sourceTimestamp','fetchedAt','dataDate','providerChange60Pct','providerMainNetInflowCNY','freshness']} | {
     'state':'historical-discovery' if historical else 'discovery-only','stateLabel':'历史广域线索，等待新快照' if historical else '广域发现，待原规则核验','sourceAgeSeconds':age,'freshness':freshness,'tradeCandidate':False,'tradeAdmitted':False,'rankBasis':'本次有效快照按已公布成交额排序，仅安排研究优先顺序',
     'relativeToSnapshotMedianPct':_text(delta),'reason':f"本次同类成交额靠前；当日涨跌{i.get('changePct')}%，供应商60日价格变化{i.get('providerChange60Pct') if i.get('providerChange60Pct') is not None else '缺失'}%（未独立核验）。",
     'nextCondition':('先核对基金类型、跟踪资产及交易制度，货币/债券/跨境ETF不能直接套用原权益ETF规则；' if kind=='etf' else '先核对主板/创业板/科创板交易制度及身份；')+'再取得同口径完整日历史，核验收盘>MA20>MA60、相对强弱、原研究评分65、流动性、回撤波动、持仓相关性及完整交易准入；未经核验不买入。',
     'invalidation':'源时间/身份/单位冲突或完整日趋势、相对强弱失效时撤销观察；未获认证的资金字段不能补足准入。',
     'suitabilityForExistingRules':'unreviewed','securityTradingRulesVerified':False,'breakoutVerified':False,'pullbackVerified':False,'entryRange':None,'stopOrTargetPrice':None,'missing':['证券/基金类型和交易规则适用性','同口径完整日历史与MA20/60','突破/回调历史证据','分红/复权与幸存者偏差核验','历史策略及完整准入','估值、独立资金和账户风险预算']})
 complete=all(v['paginationComplete'] for v in parts.values())
 status=('complete-provider-snapshot' if complete else 'partial-provider-snapshot') if items else 'unavailable'
 if collection.get('asOf')!=today and items:status='historical-'+status
 sector_rows=sorted(boards,key=lambda x:(-Decimal(x['changePct']),x['symbol']))
 sectors=[{k:i.get(k) for k in ['symbol','name','changePct','amountCNY','providerMainNetInflowCNY','sourceTimestamp','dataDate']} for i in sector_rows[:5]]
 return {'format':'goose-market-discovery','version':1,'status':status,'generatedAt':collection['generatedAt'],'viewedAt':clock.isoformat(),'asOf':collection.get('asOf'),'source':SOURCE,'synthetic':collection.get('synthetic') is True,
   'coverage':parts,'providerPaginationComplete':complete,'exchangeUniverseVerified':False,'currentDaySnapshot':collection.get('asOf')==today,'intradaySignalAllowed':False,'tradeCandidateCount':0,
   'stocks':stock_stats,'etfs':stats(funds),'marketTrend':{'basis':'有效供应商沪深股票快照等权涨跌中位数/广度，不是交易所指数趋势或完整策略信号','medianChangePct':stock_stats['medianChangePct'],'coverageComplete':parts['stock']['paginationComplete'],'asOf':collection.get('asOf')},
   'industryStrength':{'status':'provider-snapshot' if sectors else 'unavailable','items':sectors,'basis':'行业板块当日涨跌排序，层级可能重叠，不加总成交额或资金。'},
   'discoveryCount':len(candidates),'items':candidates,'fundFlow':{'status':'provider-estimate-unverified' if any(i.get('providerMainNetInflowCNY') is not None for i in valid) else 'missing','independentVerified':False,'usedForAdmission':False,'meaning':'供应商按订单类别推算主力净流入，未经独立核验；不把成交量当资金净流入。'},
   'historyCoverage':{'downloadedInThisRun':0,'breakoutValidated':0,'pullbackValidated':0},'lastAttempt':collection.get('lastAttempt',{}),'limitations':LIMITS,'sourceReferences':REFERENCE_SOURCES}

def _folder(root):
 base=_safe_path(Path(root)/'investment/work/market-discovery',missing=True);base.mkdir(parents=True,exist_ok=True);return base

def _load(root,now):
 base=_folder(root);pointer=base/'latest.json'
 if not pointer.exists():return None
 doc=json.loads(_safe_path(pointer,file=True).read_text(encoding='utf8'))
 if not re.fullmatch('[0-9a-f]{32}',doc.get('run','')) or not re.fullmatch('[0-9a-f]{64}',doc.get('sha256','')):raise ValueError('SCAN_CACHE_POINTER_INVALID')
 file=_safe_path(base/'runs'/doc['run']/'collection.json',file=True)
 if file.stat().st_size>16*1024*1024:raise ValueError('SCAN_CACHE_TOO_LARGE')
 raw=file.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=doc['sha256']:raise ValueError('SCAN_CACHE_HASH_MISMATCH')
 value=json.loads(raw)
 if value.get('format')!='goose-market-discovery-collection' or value.get('version')!=1 or value.get('synthetic'):raise ValueError('SCAN_CACHE_INVALID')
 if doc.get('lastAttempt'):
  value['lastAttempt']=doc['lastAttempt']
 return value

def load_cached(root,*,now=None):
 """Read only. Never starts network requests on the 14:38 critical path."""
 clock=_clock(now)
 try:cached=_load(root,clock)
 except (ValueError,OSError,KeyError,TypeError):return unavailable('SCAN_CACHE_INVALID',now=clock)
 if cached is None:return unavailable('SCAN_CACHE_MISSING',now=clock)
 result=summarize(cached,now=clock);result['cacheReused']=True
 result['cacheAgeSeconds']=max(0,round((clock-datetime.fromisoformat(cached['generatedAt'])).total_seconds(),3))
 return result

def unavailable(code,*,now=None):
 clock=_clock(now)
 groups={k:{'label':v['label'],'providerTotal':None,'downloadedUnique':0,'fieldValidated':0,'excludedOrUnverified':0,'pagesReceived':0,'pagesExpected':None,'paginationComplete':False,'coveragePct':None,'sourceDates':[],'codes':[code]} for k,v in SOURCES.items()}
 return summarize({'generatedAt':clock.isoformat(),'asOf':None,'items':[],'groups':groups,'synthetic':False,'lastAttempt':{'code':code}},now=clock)

def generate(root,*,now=None,deadline=None,refresh=False,attempt=None,max_pages=MAX_PAGES):
 """Return a bounded discovery summary. `refresh=True` prewarms the cache.

 A report may call the default to reuse today's cache without new network work.
 A caller can give the existing background report host a 90-second prewarm
 budget; a 25-second report budget yields honest partial coverage if necessary.
 """
 clock=_clock(now);deadline=deadline or clock+timedelta(seconds=25)
 if deadline.tzinfo is None or not 1<=max_pages<=MAX_PAGES:raise ValueError('SCAN_BUDGET_INVALID')
 cached=None
 if attempt is None:
  try:cached=_load(root,clock)
  except (ValueError,OSError,KeyError):pass
  if not refresh and cached and datetime.fromisoformat(cached['generatedAt']).astimezone(BJ).date()==clock.astimezone(BJ).date():
   result=summarize(cached,now=clock);result['cacheReused']=True;return result
 base=_folder(root);run_id=uuid.uuid4().hex;run=base/'runs'/run_id;run.mkdir(parents=True,exist_ok=False)
 request=attempt or _attempt;observations=[];pages={kind:{} for kind in SOURCES};rate_stop=threading.Event();resumed_pages=0
 # Reuse successful immutable post-close pages from the same day only. Before
 # reusing a cohort, re-fetch and compare its complete first page; any change
 # forces a fresh cohort. This avoids replaying a truncated scan as complete.
 seed={kind:{} for kind in SOURCES}
 if cached and refresh and clock.astimezone(BJ).time().replace(tzinfo=None)>=time(15,30) and cached.get('asOf')==clock.astimezone(BJ).date().isoformat() and datetime.fromisoformat(cached['generatedAt']).astimezone(BJ).time().replace(tzinfo=None)>=time(15,30):
  try:
   pointer=json.loads((base/'latest.json').read_text(encoding='utf8'));oldrun=_safe_path(base/'runs'/pointer['run'],file=False)
   anchored={i.get('rawSha256') for i in cached['items']}
   for obs in json.loads((oldrun/'observations.json').read_text(encoding='utf8')):
    kind=obs.get('kind');page=obs.get('page');name=obs.get('rawFile','')
    if kind not in SOURCES or type(page) is not int or obs.get('rawSha256') not in anchored or not re.fullmatch(r'(stock|etf|industry)-\d+\.raw',name):continue
    data=_safe_path(oldrun/name,file=True).read_bytes()
    if hashlib.sha256(data).hexdigest()!=obs['rawSha256']:continue
    seed[kind][page]=(parse_page(data,kind),obs,data)
  except (ValueError,OSError,KeyError,TypeError):seed={kind:{} for kind in SOURCES}
 def fetch(job):
  kind,page=job
  if rate_stop.is_set():return kind,page,{'source':SOURCE,'kind':kind,'page':page,'httpStatus':None,'errors':['SCAN_RATE_LIMIT_STOP_NO_REQUEST'],'receivedAt':None},None
  obs,raw=request(SOURCE,kind+':'+str(page),_url(kind,page),lambda b:parse_page(b,kind),deadline=deadline)
  obs=dict(obs);value=obs.pop('value',None);obs['kind']=kind;obs['page']=page
  if obs.get('httpStatus')==429:rate_stop.set()
  if raw is not None:
   raw_name=kind+'-'+str(page)+'.raw';(run/raw_name).write_bytes(raw);obs['rawFile']=raw_name
  return kind,page,obs,value
 with ThreadPoolExecutor(max_workers=3) as pool:first=list(pool.map(fetch,[(k,1) for k in SOURCES]))
 pending=[]
 for kind,page,obs,value in first:
  observations.append(obs)
  if value:
   pages[kind][page]=(value,obs)
   page_count=(value['total']+PAGE_SIZE-1)//PAGE_SIZE
   previous=seed[kind].get(1)
   same=previous and previous[0]==value
   if same:
    for n,(prior,prior_obs,raw) in seed[kind].items():
     if n==1 or n>min(page_count,max_pages) or prior['total']!=value['total']:continue
     old=dict(prior_obs);old['cachePageReused']=True;old['reusedInRun']=run_id
     (run/old['rawFile']).write_bytes(raw);observations.append(old);pages[kind][n]=(prior,old);resumed_pages+=1
   pending.extend((kind,p) for p in range(2,min(page_count,max_pages)+1) if p not in pages[kind])
 # Four workers bound requests, payload and pressure; no per-symbol crawl.
 with ThreadPoolExecutor(max_workers=4) as pool:
  for kind,page,obs,value in pool.map(fetch,pending):
   observations.append(obs)
   if value:pages[kind][page]=(value,obs)
 finished=_clock(None) if attempt is None else clock;items=[];groups={}
 for kind in SOURCES:
  current=pages[kind];values=[v[0] for v in current.values()];totals={v['total'] for v in values};expected=next(iter(totals)) if len(totals)==1 else None
  seen=set();duplicates=set();group_items=[]
  for page,(value,obs) in sorted(current.items()):
   for item in value['items']:
    if item['symbol'] in seen:duplicates.add(item['symbol']);continue
    seen.add(item['symbol']);group_items.append(_assess(item,obs,finished))
  expected_pages=(expected+PAGE_SIZE-1)//PAGE_SIZE if expected else None
  complete=bool(expected and not duplicates and len(seen)==expected and sorted(current)==list(range(1,expected_pages+1)))
  errors=[code for obs in observations if obs['kind']==kind for code in obs.get('errors',[])]
  if len(totals)>1:errors.append('SCAN_PROVIDER_TOTAL_CHANGED')
  if duplicates:errors.append('SCAN_CROSS_PAGE_DUPLICATE')
  if not complete:errors.append('SCAN_PAGINATION_INCOMPLETE')
  stamps=sorted(i['sourceTimestamp'] for i in group_items if i.get('sourceTimestamp'))
  receipts=sorted(obs['receivedAt'] for _,obs in current.values() if obs.get('receivedAt'))
  groups[kind]={'label':SOURCES[kind]['label'],'providerTotal':expected,'downloadedUnique':len(seen),'fieldValidated':sum(i['fieldValidated'] for i in group_items),'excludedOrUnverified':sum(not i['fieldValidated'] for i in group_items),'pagesReceived':len(current),'pagesExpected':expected_pages,'paginationComplete':complete,'coveragePct':str(round(Decimal(len(seen))*100/expected,2)) if expected else None,'sourceDates':sorted({i['dataDate'] for i in group_items if i.get('dataDate')}),'sourceTimeRange':{'earliest':stamps[0] if stamps else None,'latest':stamps[-1] if stamps else None},'fetchTimeRange':{'first':receipts[0] if receipts else None,'last':receipts[-1] if receipts else None},'snapshotSimultaneous':False,'codes':sorted(set(errors))}
  items.extend(group_items)
 dates=[i['dataDate'] for i in items if i.get('dataDate') and i['fieldValidated']]
 asof=max(set(dates),key=lambda d:(dates.count(d),d)) if dates else None
 collection={'format':'goose-market-discovery-collection','version':1,'generatedAt':finished.isoformat(),'asOf':asof,'groups':groups,'items':items,'synthetic':attempt is not None,'lastAttempt':{'startedAt':clock.isoformat(),'finishedAt':finished.isoformat(),'pageRequests':sum(not o.get('cachePageReused') for o in observations),'cachedPagesReused':resumed_pages,'httpSuccesses':sum(o.get('httpStatus')==200 and not o.get('cachePageReused') for o in observations),'deadline':deadline.isoformat()}}
 raw=_bytes(collection);(run/'collection.json').write_bytes(raw);(run/'observations.json').write_bytes(_bytes(observations))
 if attempt is None:
  pointer={'run':run_id,'sha256':hashlib.sha256(raw).hexdigest()}
  # Preserve a better same-date snapshot on a failed refresh, while recording
  # the new failure/partial attempt. Never repaint its source/fetch time.
  if cached and collection['asOf'] in (None,cached.get('asOf')) and sum(g['downloadedUnique'] for g in groups.values())<sum(g['downloadedUnique'] for g in cached['groups'].values()):
   try:
    pointer=json.loads((base/'latest.json').read_text(encoding='utf8'));pointer['lastAttempt']={**collection['lastAttempt'],'retainedEarlierCoverage':True,'refreshCodes':sorted({c for g in groups.values() for c in g['codes']})}
   except (OSError,ValueError):pass
  temp=base/('latest-'+uuid.uuid4().hex+'.tmp');temp.write_bytes(_bytes(pointer));temp.replace(base/'latest.json')
 result=summarize(collection,now=finished);result['cacheReused']=False;result['provenance']={'collectionSha256':hashlib.sha256(raw).hexdigest(),'run':run_id}
 return result
