"""Paired dependent-return uncertainty, never a winner-count significance test."""
from decimal import Decimal as D
import hashlib,math,random

def daily_returns(curve):
    if not curve:return {}
    # Floats are used only for statistical sampling, never for money balances.
    previous=D(100000000);result={}
    for r in curve:
        current=D(r['equity']);result[r['date']]=float(current/previous-1);previous=current
    return result

def paired_bootstrap(curve,benchmark,*,block=10,repetitions=499,seed='fixed'):
    left=daily_returns(curve);right=daily_returns(benchmark);days=sorted(left.keys()&right.keys());values=[left[d]-right[d] for d in days];n=len(values)
    if n<60:return {'status':'insufficient-sessions','sessions':n,'pValue':None,'blockSessions':block,'method':'paired circular moving-block bootstrap'}
    mean=sum(values)/n;centered=[v-mean for v in values];prefix=[0.0]
    for v in centered+centered:prefix.append(prefix[-1]+v)
    rng=random.Random(int(hashlib.sha256((seed+str(block)).encode()).hexdigest(),16));samples=[]
    for _ in range(repetitions):
        total=0.0;remaining=n
        while remaining:
            length=min(block,remaining);start=rng.randrange(n);total+=prefix[start+length]-prefix[start];remaining-=length
        samples.append(total/n)
    samples.sort();lo=samples[int(repetitions*.025)]+mean;hi=samples[min(repetitions-1,int(repetitions*.975))]+mean
    p=(1+sum(v>=mean for v in samples))/(repetitions+1) if mean>0 else 1.0
    return {'status':'retrospective-uncertainty-only','sessions':n,'meanDailyExcessBps':round(mean*10000,4),'meanDailyExcess95Bps':[round(lo*10000,4),round(hi*10000,4)],'pValue':round(p,6),
      'blockSessions':block,'repetitions':repetitions,'method':'paired circular moving-block bootstrap; zero-mean resampling; one-sided greater-than-zero',
      'assumptions':'weak stationarity and block dependence approximation; not a proof of future performance'}

def holm(rows):
    valid=sorted([r for r in rows if r['pValue'] is not None],key=lambda r:r['pValue']);previous=0.0;n=len(valid)
    for i,r in enumerate(valid):previous=max(previous,min(1.0,r['pValue']*(n-i)));r['holmAdjustedP']=round(previous,6)
    for r in rows:
        if r['pValue'] is None:r['holmAdjustedP']=None
    return rows

def assess(items):
    """One family per block length across correlated symbols and four rules."""
    comparisons=[]
    for item in items:
        study=item.get('study')
        if not study:continue
        strategies=study['modes']['price'];base=strategies['buy_hold']
        for name,s in strategies.items():
            if name=='buy_hold' or s.get('applicable') is False:continue
            folds={}
            for fold in ('train','validation','test'):
                a=s['partitions'][fold];b=base['partitions'][fold]
                if a is None or b is None:folds[fold]=None;continue
                folds[fold]={'excessReturnBps':a['metrics']['totalReturnBps']-b['metrics']['totalReturnBps'],
                  'blocks':[paired_bootstrap(a['equityCurve'],b['equityCurve'],block=k,seed=item['symbol']+name+fold) for k in (10,20)]}
            comparisons.append({'symbol':item['symbol'],'strategy':name,'folds':folds,'advantageStatus':'not-demonstrated','conclusion':'尚未证明优于买入持有'})
    for fold in ('train','validation','test'):
        for j in range(2):holm([r['folds'][fold]['blocks'][j] for r in comparisons if r['folds'][fold]])
    for r in comparisons:
        test=r['folds']['test'];validation=r['folds']['validation']
        r['statisticalScreenPassed']=bool(test and validation and all(f['excessReturnBps']>0 and all(b['sessions']>=126 and b.get('holmAdjustedP') is not None and b['holmAdjustedP']<.05 and b['meanDailyExcess95Bps'][0]>0 for b in f['blocks']) for f in (test,validation)))
        r['limitations']=['同类ETF和股票收益相关，不能把胜出次数当作独立试验。','Holm校正重复比较；10/20日分块对依赖性作近似，不消除选择偏差或非平稳性。','原始价格历史版本和证券池点时成员未认证；没有前瞻实测，因此统计筛选通过也不证明稳定优势。']
    return {'format':'goose-strategy-quality','version':1,'status':'not-demonstrated','conclusion':'尚未证明优于买入持有','comparisons':comparisons,'winnerCountIsEvidence':False,'familySize':len(comparisons),'prospectiveValidated':False}
