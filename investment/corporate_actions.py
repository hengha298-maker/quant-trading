"""Reviewed public event evidence, separate from private account cash flows.

Provider-adjusted prices and absence of a large jump never certify coverage.
Announcement dates use conservative next-session availability when time is absent.
"""
from datetime import date,datetime,time,timedelta
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import urlsplit
import hashlib,json,re
from .holdings import _canonical,_safe_path
from .market import BJ,xshg_sessions

AUTHORITIES={'www.sse.com.cn','static.sse.com.cn','static.cninfo.com.cn','dataclouds.cninfo.com.cn','www.nffund.com','www.southernfund.com','www.fsfund.com','www.cpicfunds.com','www.gtfund.com','www.cypc.com.cn','www.shenhuachina.com'}
TYPES={'cash-dividend','share-ratio','suspension'}

def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value) or date.fromisoformat(value).isoformat()!=value:raise ValueError('ACTION_DATE_INVALID')
    return value

def _number(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:0|[1-9]\d{0,12})(?:\.\d{1,12})?',value) or D(value)<=0:raise ValueError('ACTION_AMOUNT_OR_RATIO_INVALID')
    return D(value)

def validate(doc,*,synthetic=False):
    if doc.get('format')!='goose-corporate-actions' or doc.get('version')!=1:raise ValueError('ACTION_SCHEMA_UNSUPPORTED')
    sources=doc['sources'];seen=set();signatures=set()
    for key,s in sources.items():
        url=urlsplit(s['url'])
        if url.scheme!='https' or url.username or url.password or url.fragment or (url.hostname not in AUTHORITIES and not synthetic):raise ValueError('ACTION_SOURCE_NOT_REVIEWED')
        if not re.fullmatch(r'[a-f0-9]{64}',s['sha256']):raise ValueError('ACTION_SOURCE_HASH_INVALID')
        _date(s['publicationDate'])
        if not isinstance(s.get('locator'),str) or not s['locator']:raise ValueError('ACTION_SOURCE_LOCATOR_REQUIRED')
    for e in doc['events']:
        if not isinstance(e.get('id'),str) or e['id'] in seen or e['type'] not in TYPES or not re.fullmatch(r'\d{6}\.SH',e['symbol']):raise ValueError('ACTION_IDENTITY_OR_DUPLICATE')
        seen.add(e['id']);sig=(e['symbol'],e['type'],e.get('exDate',e.get('startDate')))
        if sig in signatures:raise ValueError('ACTION_CONFLICTING_EVENT_REQUIRES_REVIEW')
        signatures.add(sig)
        if e.get('status') not in {'verified','provisional'} or not e.get('sourceIds') or any(s not in sources for s in e['sourceIds']):raise ValueError('ACTION_EVIDENCE_REQUIRED')
        announced=_date(e['announcementDate']);available=datetime.fromisoformat(e['availableFrom'])
        if available.tzinfo is None or available.astimezone(BJ).date().isoformat()<=announced:raise ValueError('ACTION_AVAILABILITY_TOO_EARLY')
        if min(sources[s]['publicationDate'] for s in e['sourceIds'])>announced:raise ValueError('ACTION_FUTURE_DOCUMENT_NOT_ANNOUNCEMENT')
        if e['type']=='suspension':
            if not _date(e['startDate'])<=_date(e['endDate'])<_date(e['resumeDate']):raise ValueError('ACTION_SUSPENSION_DATES_INVALID')
            continue
        record,ex=_date(e['recordDate']),_date(e['exDate'])
        if not announced<=record<ex:raise ValueError('ACTION_EVENT_DATE_ORDER_INVALID')
        if e['type']=='cash-dividend':
            _number(e['cashPerShare'])
            if e.get('payDate') is not None and _date(e['payDate'])<ex:raise ValueError('ACTION_PAYMENT_BEFORE_EX_DATE')
        else:
            if _number(e['shareMultiplier'])==1:raise ValueError('ACTION_NO_SHARE_CHANGE')
            if e.get('shareAvailableDate') is not None and _date(e['shareAvailableDate'])<ex:raise ValueError('ACTION_SHARE_AVAILABILITY_INVALID')
    for c in doc['coverage']:
        if not _date(c['from'])<=_date(c['to']) or c.get('status')!='reviewed-complete-interval' or set(c.get('types',[]))!={'cash-dividend','share-ratio'}:raise ValueError('ACTION_COVERAGE_INVALID')
        if not c.get('sourceIds') or any(s not in sources for s in c['sourceIds']):raise ValueError('ACTION_COVERAGE_SOURCE_REQUIRED')
        ids={e['id'] for e in doc['events'] if e['symbol']==c['symbol'] and e['type']!='suspension' and c['from']<=e['exDate']<=c['to']}
        if set(c['eventIds'])!=ids:raise ValueError('ACTION_COVERAGE_EVENT_RECONCILIATION_FAILED')
        if c.get('cashPerShareSum') is not None and sum((D(e['cashPerShare']) for e in doc['events'] if e['id'] in ids and e['type']=='cash-dividend'),D(0))!=D(c['cashPerShareSum']):raise ValueError('ACTION_ANNUAL_DISTRIBUTION_TOTAL_CONFLICT')
    return doc

def catalog():
    return validate(json.loads((Path(__file__).with_suffix('.json')).read_text(encoding='utf8')))

def events_for(doc,symbol):return [e for e in doc['events'] if e['symbol']==symbol and e['status']=='verified']

def coverage(doc,symbol,start,end):
    _date(start);_date(end)
    spans=sorted((c['from'],c['to']) for c in doc['coverage'] if c['symbol']==symbol)
    cursor=date.fromisoformat(start);finish=date.fromisoformat(end);gaps=[]
    for a,b in spans:
        left,right=date.fromisoformat(a),date.fromisoformat(b)
        if right<cursor or left>finish:continue
        if left>cursor:gaps.append({'from':cursor.isoformat(),'to':min(finish,left-timedelta(days=1)).isoformat()})
        cursor=max(cursor,right+timedelta(days=1))
        if cursor>finish:break
    if cursor<=finish:gaps.append({'from':cursor.isoformat(),'to':finish.isoformat()})
    relevant=[e for e in events_for(doc,symbol) if e['type']!='suspension' and start<=e['exDate']<=end]
    incomplete=[e['id'] for e in relevant if e['type']=='cash-dividend' and e.get('payDate') is None or e['type']=='share-ratio' and e.get('shareAvailableDate') is None]
    incomplete.extend(e['id'] for e in doc['events'] if e['symbol']==symbol and e['type']!='suspension' and start<=e['exDate']<=end and e['status']!='verified')
    return {'complete':not gaps and not incomplete,'from':start,'to':end,'missingIntervals':gaps,'incompleteEventIds':incomplete,'verifiedEvents':len(relevant)}

def evidence_status(doc,symbol,start,end,root=None):
    result=coverage(doc,symbol,start,end);events=events_for(doc,symbol)
    ids={s for e in events for s in e['sourceIds']}|{s for c in doc['coverage'] if c['symbol']==symbol for s in c['sourceIds']}
    missing=[]
    if root is not None:
        folder=Path(root)/'investment/work/corporate-actions/official'
        for key in ids:
            s=doc['sources'][key];p=folder/s['file']
            try:
                if p.parent!=folder or hashlib.sha256(_safe_path(p,file=True).read_bytes()).hexdigest()!=s['sha256']:missing.append(key)
            except (ValueError,OSError):missing.append(key)
    result.update(sourceFilesVerified=not missing,missingSourceIds=sorted(missing),evidenceHash=hashlib.sha256(_canonical(doc)).hexdigest(),
      events=events,sources=[{'id':s,**doc['sources'][s]} for s in sorted(ids)],brokerAccountVerified=False)
    if missing:result['complete']=False
    return result

def point_time(e,day):
    return datetime.fromisoformat(e['availableFrom'])<=datetime.combine(date.fromisoformat(day),time(9,30),BJ)

def causal_series(raw,events):
    """Forward-linked event index; no later event rescales earlier observations.

    Gross entitlement is recognized on ex-date for this indicator only. This is
    neither an execution price nor proof that cash was available to reinvest.
    """
    rows=[];factor=D(1);prior=None;late=[]
    for original in raw:
        r=dict(original);day=r['date'];cash=D(0);ratio=D(1)
        for e in events:
            if e['type']=='suspension' or e['exDate']!=day:continue
            if not point_time(e,day):late.append(e['id']);continue
            if e['type']=='cash-dividend':cash+=D(e['cashPerShare'])
            else:ratio*=D(e['shareMultiplier'])
        for field in ('open','high','low','close'):r[field]=format((D(original[field])*ratio+cash)*factor,'.12f')
        factor=D(r['close'])/D(original['close']);rows.append(r);prior=day
    return {'bars':rows,'lateAnnouncementIds':sorted(set(late)),'basis':'causal-forward-event-index-not-trade-price','usesVendorQfq':False,'pointInTimeVintageCertified':False}

def valuation_rows(dataset,events):
    """Explicit non-tradable stale marks for officially evidenced suspensions.

    Never writes/fills the original market dataset. An unknown gap is fatal.
    """
    rows={r['date']:r for r in dataset['bars']};days=xshg_sessions(dataset['bars'][0]['date'],dataset['bars'][-1]['date']);result=[];previous=None
    for day in days:
        if day in rows:
            r=dict(rows[day]);r['tradable']=not any(e['type']=='suspension' and e['startDate']<=day<=e['endDate'] for e in events)
        else:
            if previous is None or not any(e['type']=='suspension' and e['startDate']<=day<=e['endDate'] for e in events):raise ValueError('ACTION_UNEXPLAINED_MARKET_GAP')
            r={'date':day,**{k:previous['close'] for k in ('open','high','low','close')},'volume':0,'publishedAt':None,'tradable':False,'valuationCarriedForward':True,'lastPriceDate':previous.get('lastPriceDate',previous['date'])}
        result.append(r);previous=r
    return result
