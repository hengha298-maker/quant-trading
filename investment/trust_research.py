"""Bounded cached research and evidence summaries for the existing dashboard."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
import hashlib,json
from decimal import localcontext
from .corporate_actions import catalog,evidence_status
from .holdings import _canonical,_safe_path
from .strategy_lab import PARTITIONS,CONFIG
from .total_return import compare

REVISION=2

def _trim(dataset,start,end):
    d=deepcopy(dataset);rows=d['bars'];eligible=[i for i,r in enumerate(rows) if start<=r['date']<=end]
    if not eligible:raise ValueError('TRUST_WINDOW_HAS_NO_SESSIONS')
    d['bars']=rows[max(0,eligible[0]-61):eligible[-1]+1];d['calendar']['sessions']=[r['date'] for r in d['bars']]
    return d

def _study(root,raw,benchmark,spec,doc,start,end,deadline):
    raw=_trim(raw,start,end);benchmark=_trim(benchmark,start,end)
    key=hashlib.sha256(_canonical({'revision':REVISION,'raw':raw['bars'],'benchmark':benchmark['bars'],'spec':spec,'evidence':doc,'config':CONFIG,'partitions':PARTITIONS,'from':start,'to':end})).hexdigest()
    folder=_safe_path(Path(root)/'investment/work/radar/labs',missing=True);folder.mkdir(parents=True,exist_ok=True);path=folder/(key+'.json')
    if path.exists():
        w=json.loads(_safe_path(path,file=True).read_text(encoding='utf8'));result=w['result']
        if w['sha256']!=hashlib.sha256(_canonical(result)).hexdigest() or result.get('inputHash')!=key or result.get('synthetic'):raise ValueError('TRUST_CACHE_INTEGRITY_FAILED')
        # A deleted/tampered evidence archive cannot stay certified via a cache.
        state=evidence_status(doc,spec['symbol'],result['from'],result['to'],root)
        if result['coverage']['complete'] and not state['complete']:raise ValueError('TRUST_EVIDENCE_CHANGED')
    else:
        if deadline and (deadline-datetime.now(timezone.utc)).total_seconds()<3:raise ValueError('TRUST_REPORT_TIME_RESERVED')
        with localcontext() as ctx:
            ctx.prec=45;result=compare(raw,benchmark,spec,doc,start=start,end=end,source_root=root,deadline=deadline)
        result.update(inputHash=key,modelRevision=REVISION)
        with path.open('xb') as f:f.write(_canonical({'result':result,'sha256':hashlib.sha256(_canonical(result)).hexdigest()}))
    return result,key

def summarize(study,key):
    result={k:v for k,v in study.items() if k!='modes'};modes={}
    for mode,strategies in study['modes'].items():
        modes[mode]={}
        for name,s in strategies.items():
            curve=s['equityCurve'];step=max(1,len(curve)//25);preview=curve[::step]
            if curve and preview[-1]!=curve[-1]:preview.append(curve[-1])
            modes[mode][name]={'metrics':s['metrics'],'applicable':s['applicable'],'unfilledOrders':sum(o['status']!='filled' for o in s['orders']),'cashSettledAtEnd':s['cashSettledAtEnd'],
              'curvePreview':[{'date':p['date'],'equity':p['equity']} for p in preview],
              'partitions':{k:({'from':p['from'],'to':p['to'],'metrics':p['metrics']} if p else None) for k,p in s['partitions'].items()}}
    result.update(modes=modes,artifactKey=key);return result

def verified_window(doc,symbol,start,end):
    spans=sorted((c['from'],c['to']) for c in doc['coverage'] if c['symbol']==symbol);merged=[]
    for a,b in spans:
        if merged and date.fromisoformat(a)<=date.fromisoformat(merged[-1][1])+timedelta(days=1):merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    candidates=[(max(a,start),min(b,end)) for a,b in merged if max(a,start)<=min(b,end)]
    return max(candidates,key=lambda x:x[1]) if candidates else None

def research(root,history,benchmark,spec,*,deadline=None):
    if history['raw']['quality']['status']!='validated' or benchmark['raw']['quality']['status']!='validated':raise ValueError('TRUST_SYNTHETIC_NOT_PRODUCTION')
    doc=catalog();start=PARTITIONS['train']['from'];end=history['raw']['bars'][-1]['date'];evidence=evidence_status(doc,spec['symbol'],start,end,root)
    current=None;bounded=None;full=None;codes=[]
    # Stable, explicitly dated evidence windows are reusable during daily runs.
    window=verified_window(doc,spec['symbol'],start,end)
    if window:
        try:
            study,key=_study(root,history['raw'],benchmark['raw'],spec,doc,*window,deadline);bounded=summarize(study,key)
        except (ValueError,OSError,KeyError) as exc:codes.append(str(exc) if str(exc).isupper() else 'TRUST_VERIFIED_WINDOW_UNAVAILABLE')
    try:
        full,key=_study(root,history['raw'],benchmark['raw'],spec,doc,start,end,deadline);current=summarize(full,key)
    except (ValueError,OSError,KeyError) as exc:codes.append(str(exc) if str(exc).isupper() else 'TRUST_CURRENT_COMPARISON_UNAVAILABLE')
    return {'status':'ready' if current else 'partial' if bounded else 'unavailable','dataTrustGrade':'B' if evidence['complete'] else 'C',
      'dataTrustLabel':'现金流区间完整，单源价格与点时版本仍有限制' if evidence['complete'] else '行情字段通过；当前含息/点时证据不完整',
      'sourceEvidence':evidence,'pointInTime':{'vendorQfqUsedInNewStudy':False,'eventAvailabilityChecked':True,'historicalPriceVintageCertified':False,'universePointInTimeCertified':False,'certified':False},
      'current':current,'verifiedWindow':bounded,'codes':codes,'advantageStatus':'not-demonstrated','advantageConclusion':'尚未证明优于买入持有'},full
