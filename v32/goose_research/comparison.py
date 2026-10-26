"""Compare provider responses without promoting instrument/unit provenance."""
from decimal import Decimal
from hashlib import sha256
from .market import validate_dataset
from .reports import canonical_bytes


def compare_sources(left, right):
    a,b=validate_dataset(left),validate_dataset(right)
    if a['sourceUrl']==b['sourceUrl'] or a['source']==b['source']:
        raise ValueError('TWO_DISTINCT_PROVIDER_ENDPOINTS_REQUIRED')
    for key in ['symbol','kind','adjustment','currency','priceUnit']:
        if a[key]!=b[key]:raise ValueError('SOURCE_COMPARISON_SCOPE_MISMATCH')
    aa={bar['date']:bar for bar in a['bars']};bb={bar['date']:bar for bar in b['bars']}
    dates=sorted(aa.keys()|bb.keys());differences=[]
    for day in dates:
        if day not in aa or day not in bb:
            differences.append({'date':day,'fields':['missing-session']});continue
        fields=[key for key in ['open','high','low','close'] if Decimal(aa[day][key])!=Decimal(bb[day][key])]
        if aa[day]['volume']!=bb[day]['volume']:fields.append('volume')
        if fields:differences.append({'date':day,'fields':fields})
    return {'format':'goose-provider-numeric-comparison','version':1,'symbol':a['symbol'],
        'from':dates[0],'to':dates[-1],'sessionsCompared':len(dates),'numericAgreement':not differences,
        'mismatchCount':len(differences),'firstMismatches':differences[:10],
        'sources':[{'source':x['source'],'url':x['sourceUrl'],'sha256':sha256(canonical_bytes(x)).hexdigest()} for x in [a,b]],
        'independentExchangeFeedProven':False,'unitVerificationEstablished':False,
        'corporateActionsVerified':False,'datasetQualityFlagsChanged':False,
        'limitation':'两家接口数值一致不证明独立原始行情授权、ETF单位或公司行为；不修改任何质量标志。'}
