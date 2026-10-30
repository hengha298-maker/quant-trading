"""Bounded public history refresh for the reviewed radar; no private uploads.

Raw prices are incrementally checked; provider-qfq is always fetched as one
whole basis when the endpoint date changes. Immutable cached snapshots preserve
old evidence. A missing session is never fabricated or labelled a suspension.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,time,timezone
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import urlencode
import hashlib,json,re,uuid
from .holdings import _safe_path,_canonical
from .market import (_attempt,_daily_parser,_decimal,_volume,QUOTE_URL,TENCENT_URL,
                     BJ,xshg_sessions,CALENDAR_SUPPORTED_THROUGH,classify_quote)
from goose_research.market import calendar_supported_through
from goose_research.market import validate_dataset,etf_anomaly_diagnostics

def catalog():
    doc=json.loads((Path(__file__).parent/'radar_catalog.json').read_text(encoding='utf8'))
    items=doc['instruments'];seen=set()
    if doc['version']!=1 or not 1<=len(items)<=30:raise ValueError('RADAR_CATALOG_INVALID')
    for s in items:
        if s['symbol'] in seen or not re.fullmatch(r'\d{6}\.SH',s['symbol']) or s['kind'] not in {'etf','stock'} or s['tick']!=('0.001' if s['kind']=='etf' else '0.01') or s['limitBps']!=1000:raise ValueError('RADAR_CATALOG_INVALID')
        seen.add(s['symbol'])
    if doc['benchmark'] not in seen:raise ValueError('RADAR_BENCHMARK_MISSING')
    return doc

def complete_session(now):
    local=now.astimezone(BJ)
    xshg_sessions(local.date().isoformat(),local.date().isoformat())
    end=local.date() if local.time().replace(tzinfo=None)>=time(15,30) else local.date()-timedelta(days=1)
    sessions=xshg_sessions((end-timedelta(days=35)).isoformat(),end.isoformat())
    if not sessions:raise ValueError('RADAR_COMPLETE_SESSION_UNKNOWN')
    return sessions[-1]

def parse_quotes(raw,specs):
    text=raw.decode('gbk');parsed={};errors={};seen=set()
    for line in text.strip().splitlines():
        m=re.fullmatch(r'v_sh(\d{6})="([^"\r\n]*)";\s*',line)
        if not m:raise ValueError('RADAR_QUOTE_FRAMING')
        symbol=m[1]+'.SH'
        if symbol not in specs or symbol in seen:raise ValueError('RADAR_QUOTE_IDENTITY')
        seen.add(symbol);fields=m[2].split('~');spec=specs[symbol]
        try:
            if not 49<=len(fields)<=120 or fields[0]!='1' or fields[2]!=symbol[:6] or fields[1] not in spec['aliases'] or 'ST' in fields[1].upper():raise ValueError('RADAR_NAME_OR_STATUS_UNREVIEWED')
            price,prev,opening,high,low=[_decimal(fields[i],tick=spec['tick']) for i in (3,4,5,33,34)]
            if not D(low)<=min(D(opening),D(price))<=max(D(opening),D(price))<=D(high):raise ValueError('RADAR_QUOTE_OHLC')
            if abs(D(price)/D(prev)-1)>D('.20'):raise ValueError('RADAR_QUOTE_SCALE_OR_LARGE_CHANGE')
            if not re.fullmatch(r'\d{14}',fields[30]):raise ValueError('RADAR_QUOTE_TIME')
            stamp=datetime.strptime(fields[30],'%Y%m%d%H%M%S').replace(tzinfo=BJ)
            volume=_volume(fields[6]);amount=D(_decimal(fields[37],zero=True))*10000
            if fields[36] and _volume(fields[36])!=volume:raise ValueError('RADAR_VOLUME_FIELDS_DISAGREE')
            if volume and not D(low)*volume*D('.995')<=amount<=D(high)*volume*D('1.005'):raise ValueError('RADAR_AMOUNT_UNIT_INCONSISTENT')
            parsed[symbol]={'symbol':symbol,'name':fields[1],'source':'tencent-quote','price':price,'previousClose':prev,'sourceTimestamp':stamp.isoformat(),
              'volume':volume,'amountCNY':str(amount),'currency':'CNY','priceUnit':'CNY-per-share','adjustment':'none',
              'timestampKind':'provider-update-not-certified-last-trade','warnings':['PUBLIC_WEB_NO_INDEPENDENT_EXCHANGE_FEED_VERIFICATION']}
        except (ValueError,KeyError) as exc:errors[symbol]=str(exc) if re.fullmatch(r'[A-Z0-9_]{1,100}',str(exc)) else 'RADAR_QUOTE_INVALID'
    for symbol in specs:
        if symbol not in parsed and symbol not in errors:errors[symbol]='RADAR_QUOTE_MISSING'
    return {'quotes':parsed,'errors':errors}

def _cached(folder,symbol):
    files=sorted(folder.glob(symbol+'-*.json')) if folder.exists() else []
    if not files:return None
    p=_safe_path(files[-1],file=True);doc=json.loads(p.read_text(encoding='utf8'))
    if doc.get('format')!='goose-radar-history' or doc.get('sha256')!=hashlib.sha256(_canonical({k:v for k,v in doc.items() if k!='sha256'})).hexdigest():raise ValueError('RADAR_CACHE_CORRUPT')
    validate_dataset(doc['raw'])
    if doc['raw']['symbol']!=symbol or doc['adjusted'].get('symbol')!=symbol or doc['adjusted'].get('adjustment')!='qfq':raise ValueError('RADAR_CACHE_IDENTITY')
    if [r['date'] for r in doc['adjusted']['bars']]!=doc['raw']['calendar']['sessions']:raise ValueError('RADAR_CACHE_BASIS_DATES')
    return doc

def collect(root,*,now=None,deadline=None,attempt=None):
    now=now or datetime.now(timezone.utc);end=complete_session(now);cat=catalog();specs={s['symbol']:s for s in cat['instruments']}
    dates=xshg_sessions((datetime.fromisoformat(end).date()-timedelta(days=1100)).isoformat(),end)[-600:]
    if len(dates)<252:raise ValueError('RADAR_HISTORY_WINDOW_TOO_SHORT')
    base=_safe_path(Path(root)/'investment/work/radar',missing=True)
    # Collection writes only below this explicit ignored project work namespace.
    base.mkdir(parents=True,exist_ok=True);cache=base/'market';cache.mkdir(exist_ok=True)
    run=base/'observations'/(now.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]);run.mkdir(parents=True)
    request=attempt or _attempt;observations=[]
    def fetch(source,symbol,url,parser):
        obs,raw=request(source,symbol,url,parser,deadline=deadline)
        if raw is not None:
            name=symbol+'-'+source+'-'+uuid.uuid4().hex[:6]+'.raw';(run/name).write_bytes(raw);obs['rawFile']=name
        return obs
    quote_obs=fetch('tencent-radar-quotes','pool',QUOTE_URL+','.join('sh'+s[:6] for s in specs),lambda raw:parse_quotes(raw,specs))
    value=quote_obs.pop('value');observations.append(quote_obs);quotes={};qerrors=value['errors'] if value else {s:'RADAR_QUOTE_REQUEST_FAILED' for s in specs}
    for s,q in (value or {}).get('quotes',{}).items():quotes[s]=classify_quote(q,quote_obs,now if attempt else datetime.now(timezone.utc))
    def one(spec):
        symbol=spec['symbol'];diags=[];quote=quotes.get(symbol)
        try:
            if deadline and datetime.now(timezone.utc)>=deadline:raise ValueError('RADAR_BUDGET_EXHAUSTED')
            prior=_cached(cache,symbol)
            if prior and attempt is None and prior['raw']['quality']['status']!='validated':raise ValueError('RADAR_CACHED_TEST_DATA_NOT_PRODUCTION')
            if quote is None:raise ValueError(qerrors.get(symbol,'RADAR_IDENTITY_NOT_VERIFIED'))
            if quote['quality']['status']=='blocked' or quote['volume']<=0:raise ValueError('RADAR_INACTIVE_OR_STATUS_UNVERIFIED')
            if prior and prior['through']==end:
                stamp=quote['sourceTimestamp'][:10];reference=quote['price'] if stamp==end else quote['previousClose'] if stamp>end and stamp==now.astimezone(BJ).date().isoformat() else None
                if reference is None or abs(D(prior['raw']['bars'][-1]['close'])-D(reference))>D(spec['tick']):raise ValueError('RADAR_CACHED_SCALE_OR_EXRIGHTS_REFERENCE_REQUIRES_REVIEW')
                return symbol,prior,{'status':'cached-complete-session','through':end,'historyFetchedAt':prior['raw']['fetchedAt'],'quote':quote},diags
            provider='sh'+symbol[:6];raw_bars=None;adjusted=None
            for basis in ('none','qfq'):
                begin=dates[0]
                if basis=='none' and prior and prior['through']<end:begin=max(dates[0],prior['raw']['bars'][-21]['date'])
                url=TENCENT_URL+'?'+urlencode({'_var':'goose','param':f'{provider},day,{begin},{end},640,{"" if basis=="none" else "qfq"}'})
                obs=fetch('tencent-radar-'+basis,symbol,url,lambda raw,b=basis,a=begin:_daily_parser(raw,symbol,a,end,b))
                bars=obs.pop('value');diags.append(obs)
                if bars is None:raise ValueError('RADAR_'+basis.upper()+'_HISTORY_FAILED')
                if basis=='none':
                    if prior and begin>dates[0]:
                        old={r['date']:r for r in prior['raw']['bars']};revised=[r['date'] for r in bars if r['date'] in old and r!=old[r['date']]]
                        if revised:raise ValueError('RADAR_RAW_HISTORY_REVISED_REQUIRES_REVIEW')
                        bars=[r for r in prior['raw']['bars'] if dates[0]<=r['date']<begin]+bars
                    raw_bars=bars;raw_obs=obs
                else:adjusted={'symbol':symbol,'adjustment':'qfq','bars':bars,'source':'tencent','sourceUrl':TENCENT_URL,'fetchedAt':obs['receivedAt'],'quality':{'status':'validated-basis-only','ohlcAndCalendarValidated':True},'totalReturnVerified':False}
            if [r['date'] for r in raw_bars]!=dates or [r['date'] for r in adjusted['bars']]!=dates:raise ValueError('RADAR_HISTORY_GAP')
            if any(D(r[k])%D(spec['tick']) for r in raw_bars for k in ('open','high','low','close')):raise ValueError('RADAR_RAW_TICK_MISMATCH')
            stamp=quote['sourceTimestamp'][:10];reference=quote['price'] if stamp==end else quote['previousClose'] if stamp>end and stamp==now.astimezone(BJ).date().isoformat() else None
            if reference is None or abs(D(raw_bars[-1]['close'])-D(reference))>D(spec['tick']):raise ValueError('RADAR_QUOTE_DAILY_SCALE_OR_EXRIGHTS_REFERENCE_REQUIRES_REVIEW')
            raw={'format':'goose-market-dataset','version':1,'symbol':symbol,'kind':spec['kind'],'source':'tencent','sourceUrl':TENCENT_URL,'adjustment':'none','currency':'CNY','priceUnit':'CNY-per-share','fetchedAt':raw_obs['receivedAt'],
                 'calendar':{'name':'XSHG','sessions':dates,'supportedThrough':calendar_supported_through()},'bars':raw_bars,
                 'quality':{'status':'validated','crossValidated':False,'unitVerified':True,'corporateActionsVerified':False,'unitEvidence':'Reviewed exchange price tick/share units plus public quote/raw daily scale consistency, not independent feed certification','warnings':['PRICE_ONLY_NO_VERIFIED_DIVIDEND_TOTAL_RETURN','CURRENT_SURVIVING_MANUAL_POOL_NOT_POINT_IN_TIME_UNIVERSE']}}
            raw=validate_dataset(raw)
            if spec['kind']=='etf' and etf_anomaly_diagnostics(raw)['status']=='blocked':raise ValueError('RADAR_ETF_SCALE_OR_LARGE_MOVE_REVIEW')
            if spec['kind']=='stock' and any(abs(D(raw_bars[i]['close'])/D(raw_bars[i-1]['close'])-1)>D('.25') for i in range(1,len(raw_bars))):raise ValueError('RADAR_STOCK_ACTION_OR_SCALE_REQUIRES_REVIEW')
            if any(r['volume']<=0 for r in raw_bars[-20:]):raise ValueError('RADAR_ZERO_VOLUME_OR_SUSPENSION_UNVERIFIED')
            if attempt is not None:raw['quality']['status']='synthetic'
            doc={'format':'goose-radar-history','version':1,'symbol':symbol,'through':end,'raw':raw,'adjusted':adjusted}
            doc['sha256']=hashlib.sha256(_canonical(doc)).hexdigest()
            name=symbol+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'
            with (cache/name).open('xb') as f:f.write(_canonical(doc))
            return symbol,doc,{'status':'validated-public-history','through':end,'historyFetchedAt':raw['fetchedAt'],'quote':quote},diags
        except (ValueError,OSError,KeyError,IndexError) as exc:
            code=str(exc) if re.fullmatch(r'[A-Z0-9_]{1,120}',str(exc)) else 'RADAR_HISTORY_VALIDATION_FAILED'
            return symbol,None,{'status':'missing-or-abnormal','through':end,'codes':[code],'suspensionConfirmed':False},diags
    histories={};quality={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for symbol,doc,state,diags in pool.map(one,cat['instruments']):
            histories[symbol]=doc;quality[symbol]=state;observations.extend(diags)
    result={'format':'goose-radar-market','version':1,'through':end,'generatedAt':now.isoformat(),'catalog':cat,'histories':histories,'quality':quality,'observations':observations,'independentFeedsVerified':False,'synthetic':attempt is not None}
    (run/'collection.json').write_bytes(_canonical({'through':end,'quality':quality,'observations':observations}))
    return result
