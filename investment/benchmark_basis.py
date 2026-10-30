"""Fail-closed symmetric momentum policy, including the indicator warm-up.

Coverage is a prerequisite, not a warning. Annual reports audit completeness
retrospectively; only individual implementation notices enter causal signals.
"""
from .corporate_actions import coverage,evidence_status,events_for,point_time

PRICE_BASIS='unadjusted-price-both-sides'
TOTAL_BASIS='gross-causal-total-return-both-sides'
RELATIVE='relative_momentum_60'

def proof(doc,symbol,start,end,root,*,synthetic=False):
    if synthetic:
        return {**coverage(doc,symbol,start,end),'sourceFilesVerified':True,'missingSourceIds':[],'syntheticEvidenceOnly':True}
    from pathlib import Path
    state=evidence_status(doc,symbol,start,end,root or Path(__file__).resolve().parents[1],scope_to_interval=True)
    return {k:state[k] for k in ('complete','from','to','missingIntervals','incompleteEventIds','verifiedEvents','sourceFilesVerified','missingSourceIds')}

def policy(doc,symbol,benchmark,start,end,root,*,synthetic=False):
    subject=proof(doc,symbol,start,end,root,synthetic=synthetic)
    base=proof(doc,benchmark,start,end,root,synthetic=synthetic)
    reasons=[]
    if not subject['complete']:reasons.append('RELATIVE_SUBJECT_ACTION_COVERAGE_OR_HASH_INCOMPLETE')
    if not base['complete']:reasons.append('RELATIVE_BENCHMARK_ACTION_COVERAGE_OR_HASH_INCOMPLETE')
    for code,target in [('SUBJECT',symbol),('BENCHMARK',benchmark)]:
        if any(e['type']!='suspension' and start<=e['exDate']<=end and not point_time(e,e['exDate']) for e in events_for(doc,target)):reasons.append('RELATIVE_'+code+'_EVENT_NOT_AVAILABLE_AT_EX')
    return {'revision':1,'subject':symbol,'benchmark':benchmark,'requiredFrom':start,'requiredTo':end,
      'includesWarmup':True,'priceBasis':PRICE_BASIS,'totalBasis':TOTAL_BASIS,'totalAllowed':not reasons,
      'subjectCoverage':subject,'benchmarkCoverage':base,'blockReasons':reasons,
      'taxBasis':'gross entitlement index on both sides; investor tax only affects the separately simulated account',
      'meaning':'价格相对动量：两侧未复权价格，不是总回报动量。含息相对动量：两侧同一税前因果权益指数；必须核验完整预热及研究区间原件。'}

def blocked(reason,basis):
    from .strategy_lab import PARTITIONS
    return {'status':'blocked','applicable':False,'notApplicableReason':reason,'signalBasis':basis,
      'metrics':None,'equityCurve':[],'trades':[],'orders':[],'eventCashFlows':[],
      'cashSettledAtEnd':None,'partitions':dict.fromkeys(PARTITIONS),'usesBrokerAccount':False}
