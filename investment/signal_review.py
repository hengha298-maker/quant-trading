"""Evidence audit and append-only semantic research events, never trades."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal as D
from statistics import median
import json,hashlib,re
from .holdings import _safe_path,_canonical
from .signal_tracking import _next_sessions,_validated,_evaluate,histories,PROSPECTIVE_RECORD_MAX_SECONDS
from .signal_provenance import load_reports,classify,REVISION as PROVENANCE_REVISION

def semantic_id(s,cohort):
 fields={k:s.get(k) for k in ['symbol','modelFingerprint','researchThrough','state','entryCondition','metConditions','unmetConditions','invalidation']}
 return hashlib.sha256(_canonical({'cohort':cohort,'condition':fields})).hexdigest()

def audit(root,*,current_report=None,now=None,persist=False,datasets=None):
 clock=now or datetime.now(timezone.utc);base=_safe_path(Path(root)/'investment/work',missing=True);rows=[]
 reports=load_reports(root,current_report)
 for p in (base/'signals').glob('*.json'):
  try:
   if p.stat().st_size>2*1024*1024:continue
   b=json.loads(_safe_path(p,file=True).read_bytes());r,archive=reports.get(b.get('reportId'),({},None))
   for s in b['signals']:
    binding=classify(b,s,r,clock);proof=binding['proof'];cohort=binding['cohort'];q=binding['quote'];features=binding['features']
    first=_next_sessions(s['signalDay']) if proof else [];execution=(first[0]+'T09:30:00+08:00') if first else None
    row={**s,'eventId':semantic_id(s,cohort),'cohort':cohort,'recordedAt':b['recordedAt'],'sourceProof':proof,'archiveSha256':archive,
     'provenanceRevision':PROVENANCE_REVISION,'provenanceCodes':binding['codes'],
     'quoteAtSignal':{k:q.get(k) for k in ['price','source','sourceTimestamp','fetchedAt','adjustment']},
     'technicalAtSignal':{k:features.get(k) for k in ['rawClose','rawMa20','rawMa60','momentum20','relative60','volumeRatio','vol60','drawdown126']},
     'technicalEvidenceProvenance':'原始源报告opportunityRadar.items.features；不使用后续缓存补当时指标',
     'marketEnvironmentAtSignal':'unverified',
     'relativeMomentumProxyAtSignal':'positive' if D(str(features.get('relative60') or 0))>0 else 'nonpositive' if features.get('relative60') is not None else 'unavailable',
     'marketEnvironmentMeaning':'相对60日动量仅是个体代理，不能替代当时全市场牛熊、行业与资金环境。',
     'assumedExecutionAt':execution,'executionMeaning':'下个交易日开盘参考，不是实际成交','validForEventStudy':proof and cohort in {'prospective','reconstructed'}}
    rows.append(row)
  except (OSError,ValueError,KeyError,TypeError,ArithmeticError,AttributeError):continue
 rows.sort(key=lambda r:(str(r.get('signalAt','')),str(r.get('recordedAt','')),r['eventId']));unique={}
 for r in rows:unique.setdefault(r['eventId'],r)
 data,errors=_validated(datasets if datasets is not None else histories(root),clock)
 events=[];latest={}
 for r in rows:
  if r['validForEventStudy']:latest[r['symbol']]=r
 for r in unique.values():
  if r['validForEventStudy']:r={**r,'followUp':_evaluate(r,data,errors,clock)}
  newest=latest.get(r['symbol']);same_model=newest and newest.get('modelFingerprint')==r.get('modelFingerprint')
  was=set(r.get('metConditions') or []);still=set((newest or {}).get('metConditions') or [])
  invalid=bool(same_model and newest['signalAt']>r['signalAt'] and was-still)
  r={**r,'lifecycle':'invalidated-by-later-evidence' if invalid else 'dated-observation-not-current' if newest and newest['eventId']!=r['eventId'] else 'latest-research-observation',
     'triggerVerified':False,'tradeAdmitted':False,'triggerMeaning':'仅记录既有条件，不从日线反推盘中实际触发'}
  events.append(r)
 if persist:
  folder=_safe_path(base/'research-events',missing=True);folder.mkdir(parents=True,exist_ok=True)
  for r in events:
   path=folder/(r['eventId']+'.json')
   if not path.exists():
    # Immutable event facts only. Evolving outcomes are separate versions.
    event={k:v for k,v in r.items() if k not in {'followUp','lifecycle'}}
    with path.open('x',encoding='utf8') as f:json.dump(event,f,ensure_ascii=False,indent=2)
   # Preserve earlier event files including missing fields. Evidence recovered
   # from the immutable source report is a separately hashed append-only version.
   evidence={k:r.get(k) for k in ['eventId','sourceReportId','sourceEvidenceSha256','archiveSha256','quoteAtSignal','technicalAtSignal','technicalEvidenceProvenance']}
   versions=_safe_path(base/'research-event-evidence',missing=True);versions.mkdir(parents=True,exist_ok=True)
   version_path=versions/(r['eventId']+'-'+hashlib.sha256(_canonical(evidence)).hexdigest()+'.json')
   if not version_path.exists():
    with version_path.open('x',encoding='utf8') as f:json.dump(evidence,f,ensure_ascii=False,indent=2)
  outcomes=_safe_path(base/'research-outcomes',missing=True);outcomes.mkdir(parents=True,exist_ok=True)
  version=hashlib.sha256(_canonical(events)).hexdigest();p=outcomes/(version+'.json')
  if not p.exists():
   with p.open('x',encoding='utf8') as f:json.dump({'evaluatedAt':clock.isoformat(),'events':events},f,ensure_ascii=False,indent=2)
 counts={c:sum(r['cohort']==c for r in rows) for c in ['prospective','reconstructed','simulation','unverified']}
 statistics=[]
 def fmt(values):return format(sum(values)/len(values),'.4f') if values else None
 for cohort in ['prospective','reconstructed']:
  group=[e for e in events if e['cohort']==cohort and e['validForEventStudy']]
  for horizon in (1,5,20):
   outcomes=[e.get('followUp',{}).get('horizons',{}).get(str(horizon),{}) for e in group]
   valid=[o for o in outcomes if o.get('status')=='observed-price-only' and o.get('priceReturnPct') is not None]
   returns=[D(o['priceReturnPct']) for o in valid];wins=[v for v in returns if v>0];losses=[v for v in returns if v<0]
   excess=[D(o['excessVsBenchmarkPct']) for o in valid if o.get('excessVsBenchmarkPct') is not None]
   draw=[D(o['maximumCloseDrawdownPct']) for o in valid if o.get('maximumCloseDrawdownPct') is not None]
   statistics.append({'cohort':cohort,'horizon':horizon,'uniqueEvents':len(group),'maturedUsable':len(returns),'positivePriceEventRatePct':format(D(len(wins))/len(returns)*100,'.2f') if returns else None,
    'meanPriceEventReturnPct':fmt(returns),'medianPriceEventReturnPct':format(median(returns),'.4f') if returns else None,'meanExcessVsBenchmarkPct':fmt(excess),'benchmarkComparableCount':len(excess),
    'averageWinLossRatio':format((sum(wins)/len(wins))/abs(sum(losses)/len(losses)),'.4f') if wins and losses else None,'worstEventCloseDrawdownPct':format(min(draw),'.4f') if draw else None,
    'strategyWinRate':None,'reliability':'少于30个成熟事件，统计不可靠' if len(returns)<30 else '事件可能相关，仍非独立交易试验；未证明策略有效',
    'basis':'stable-semantic-events; none; next-session-open; price-only-not-net-total-return'})
 return {'format':'goose-signal-evidence-audit','version':1,'auditedAt':clock.isoformat(),'observationCount':len(rows),'uniqueEventCount':len(events),'duplicateObservationCount':len(rows)-len(events),'cohorts':counts,'evidenceCompleteness':{'eventsWithOriginalQuote':sum(bool(e['quoteAtSignal'].get('price')) for e in events),'eventsWithOriginalTechnical':sum(bool(e['technicalAtSignal'].get('rawMa20')) for e in events),'basis':'只恢复源报告中当时已保存的数据，不用新行情补旧信号'},'statistics':statistics,'marketRegimePerformance':'未形成可核验的历史市场环境分组，不以个体相对动量替代。','events':events}

def scorecard(report):
 rows=[]
 for i in report.get('opportunityRadar',{}).get('items',[]):
  lab=i.get('lab') or {};strategies=lab.get('strategies') or {};base=strategies.get('buy_hold') or {}
  for name,s in strategies.items():
   if name=='buy_hold' or s.get('applicable') is False:continue
   folds={}
   for fold in ['train','validation','test']:
    a=s.get('partitions',{}).get(fold) or {};b=base.get('partitions',{}).get(fold) or {};m=a.get('metrics') or {};bm=b.get('metrics') or {}
    aligned=a.get('from')==b.get('from') and a.get('to')==b.get('to') and bool(m) and bool(bm)
    folds[fold]={'from':a.get('from'),'to':a.get('to'),'sessions':m.get('tradingDays'),'returnBps':m.get('totalReturnBps'),'drawdownBps':m.get('maxDrawdownBps'),'tradeCount':m.get('tradeCount'),'costCents':m.get('cost'),'excessVsBuyHoldBps':m['totalReturnBps']-bm['totalReturnBps'] if aligned else None}
   v=folds['validation']['excessVsBuyHoldBps'];t=folds['test']['excessVsBuyHoldBps']
   verdict='验证与留出均未胜出：不列优先策略研究' if v is not None and t is not None and v<=0 and t<=0 else '留出表现落后，未证明优势' if t is not None and t<=0 else '留出价格表现较好，但尚未证明稳定优势' if t is not None else '证据不足'
   rows.append({'symbol':i['symbol'],'name':i['name'],'strategy':name,'folds':folds,'conclusion':verdict,'liveRulesChanged':False,'costAssumptions':lab.get('config'),'totalReturnVerified':False,'prospectiveValidated':False})
 return {'rows':rows,'parametersTuned':False,'newStrategiesPromoted':False,'limitations':['旧固定参数和训练/验证/留出区间保持不变；不以本次成绩重调参数。','未验证回调、ETF轮动及反转策略不进入正式信号。','价格历史有事后修订及幸存者偏差，价格收益不是完整含息总回报。']}

def enrich(report,root,*,persist=False):
 a=audit(root,current_report=report,persist=persist);cards=scorecard(report)
 grades=[]
 for i in report.get('marketDiscovery',{}).get('items',[]):
  h=i.get('historyReview') or {};verified=h.get('status')=='validated-isolated-price-research';near=(h.get('breakout') or {}).get('nearOriginalBand') is True
  grade='B' if verified and near else 'C' if verified else 'D'
  grades.append({'symbol':i['symbol'],'name':i['name'],'kind':i['kind'],'grade':grade,'meaning':'仅研究排序，不是交易准入','dataThrough':h.get('through'),'reason':'完整日证据可读，仍有原门槛或历史验证缺口' if verified else '日线、单位、复权或交易制度未完整核验','tradeAdmitted':False})
 report['signalReview']={k:v for k,v in a.items() if k!='events'}
 report['signalReview']['latestEvents']=[{k:r.get(k) for k in ['eventId','symbol','name','cohort','signalAt','quoteAtSignal','technicalAtSignal','marketEnvironmentAtSignal','assumedExecutionAt','lifecycle','followUp']} for r in a['events'][-12:]]
 report['strategyScorecard']=cards;report['researchGrades']={'items':grades,'admissionThresholdsChanged':False,'gradeA':0,'gradeMeaning':'A证据较完整、B接近原研究条件、C普通研究、D数据不足；评级不替代准入。'}
 return a,cards
