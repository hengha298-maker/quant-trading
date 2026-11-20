"""Read a hashed historical raw response without pretending it was qfq.

This does not restore a symbol to the current radar or overwrite market caches.
Only a separately audited cash-flow interval can unlock a bounded study.
"""
from pathlib import Path
from decimal import Decimal as D
import hashlib,json
from .holdings import _safe_path
from .market import _daily_parser,TENCENT_URL,calendar_supported_through
from .radar_market import parse_quotes,catalog
from goose_research.market import validate_dataset,etf_anomaly_diagnostics,xshg_sessions

def archived_raw(root,spec):
    base=_safe_path(Path(root)/'investment/work/radar/observations',missing=True)
    if not base.exists():raise ValueError('ARCHIVED_RAW_NOT_FOUND')
    for folder in sorted(base.iterdir(),reverse=True):
        if not folder.is_dir() or folder.is_symlink() or folder.is_junction():continue
        try:
            c=json.loads(_safe_path(folder/'collection.json',file=True).read_text(encoding='utf8'));observations=c['observations']
            obs=next(o for o in observations if o['source']=='tencent-radar-none' and o['symbol']==spec['symbol'] and o.get('rawFile'))
            quote=next(o for o in observations if o['source']=='tencent-radar-quotes' and o.get('rawFile'))
        except (OSError,ValueError,KeyError,StopIteration):continue
        def raw(o):
            name=o['rawFile']
            if Path(name).name!=name:raise ValueError('ARCHIVE_PATH_INVALID')
            data=_safe_path(folder/name,file=True).read_bytes()
            if hashlib.sha256(data).hexdigest()!=o['rawSha256']:raise ValueError('ARCHIVE_HASH_INVALID')
            return data
        from datetime import date,timedelta
        end=c['through'];days=xshg_sessions((date.fromisoformat(end)-timedelta(days=1100)).isoformat(),end)[-600:]
        bars=_daily_parser(raw(obs),spec['symbol'],days[0],end,'none')
        q=parse_quotes(raw(quote),{s['symbol']:s for s in catalog()['instruments']})['quotes'][spec['symbol']]
        reference=q['price'] if q['sourceTimestamp'][:10]==end else q['previousClose']
        if abs(D(reference)-D(bars[-1]['close']))>D(spec['tick']):raise ValueError('ARCHIVE_PRICE_SCALE_MISMATCH')
        if any(D(r[k])%D(spec['tick']) for r in bars for k in ('open','high','low','close')):raise ValueError('ARCHIVE_PRICE_TICK_MISMATCH')
        ds={'format':'goose-market-dataset','version':1,'symbol':spec['symbol'],'kind':spec['kind'],'source':'tencent','sourceUrl':TENCENT_URL,'adjustment':'none','currency':'CNY','priceUnit':'CNY-per-share','fetchedAt':obs['receivedAt'],
            'calendar':{'name':'XSHG','sessions':days,'supportedThrough':calendar_supported_through()},'bars':bars,
            'quality':{'status':'validated','crossValidated':False,'unitVerified':True,'corporateActionsVerified':False,'unitEvidence':'Hashed archived raw response; explicit unadjusted CNY fields, exchange tick and same-feed quote scale checks; no qfq inference','warnings':['ARCHIVED_RAW_NOT_CURRENT_QUOTES','NOT_INDEPENDENT_FEED_VERIFICATION']}}
        validate_dataset(ds)
        if spec['kind']=='etf' and etf_anomaly_diagnostics(ds)['status']=='blocked':raise ValueError('ARCHIVE_ETF_ANOMALY_STILL_BLOCKED')
        return {'raw':ds,'adjusted':None,'archiveProvenance':{'rawSha256':obs['rawSha256'],'quoteSha256':quote['rawSha256'],'through':end,'currentRadarRestored':False}}
    raise ValueError('ARCHIVED_RAW_NOT_FOUND')
