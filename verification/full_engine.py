"""Real public cold benchmark. Fictional account; no production I/O or clocks."""
import sys,json,time,hashlib,tempfile,threading,re,resource
from collections import Counter
from pathlib import Path
from datetime import datetime,timedelta,timezone
from urllib.parse import urlsplit
from unittest.mock import patch
from contextlib import ExitStack

ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT,ROOT/'v32',ROOT/'v33'):sys.path.insert(0,str(p))
from investment import engine,market,radar,radar_market,market_scan,market_scan_history,holdings,v2_history,daily,strategy_lab

ALLOWED={'qt.gtimg.cn','proxy.finance.qq.com','push2his.eastmoney.com','push2delay.eastmoney.com'}
def safe_code(error):
 s=str(error)
 return s if re.fullmatch('[A-Z0-9_]{1,120}',s) else type(error).__name__
def window(now):
 local=now.astimezone(market.BJ);day=local.date().isoformat()
 trading=day in daily._sessions(day)
 return {'checkedAt':now.isoformat(),'beijingTime':local.isoformat(),'isTradingDay':trading,'originalAnalysisWindowOpen':trading and daily.START<=local.time().replace(tzinfo=None)<daily.ANALYSIS_CUTOFF,'clockAltered':False}

def main():
 assert sys.platform=='linux','ACTUAL_LINUX_REQUIRED'
 start=time.monotonic();clock=datetime.now(timezone.utc);gate=window(clock)
 summary={'format':'goose-full-real-cold-validation','platform':sys.platform,'startedAt':clock.isoformat(),'productionWindow':gate,'privateInputs':False,'account':'fictional-validation-only','productionWrites':0,'barkRequests':0,'thresholdsChanged':False,'clockAltered':False,'cachedInputsRestored':False,'stages':[]}
 if '--require-live-window' in sys.argv and not gate['originalAnalysisWindowOpen']:
  summary.update(status='blocked',reason='OUTSIDE_ORIGINAL_ANALYSIS_WINDOW',networkRequests=0)
  print('GOOSE_FULL_RESULT='+json.dumps(summary,separators=(',',':')));return 2
 observations=[];lock=threading.Lock();stopped=set();requests=0
 cutoff=clock+timedelta(seconds=330)
 original_attempt=market._attempt
 def bounded(source,symbol,url,parser,deadline=None):
  nonlocal requests
  host=urlsplit(url).hostname
  if urlsplit(url).scheme!='https' or host not in ALLOWED:raise AssertionError('NON_PUBLIC_MARKET_DESTINATION_FORBIDDEN')
  for attempt in (1,2):
   with lock:
    blocked=host in stopped or requests>=160 or datetime.now(timezone.utc)>=cutoff
    if not blocked:requests+=1
   if blocked:
    now=datetime.now(timezone.utc).isoformat()
    return {'source':source,'symbol':symbol,'endpoint':url.split('?')[0],'requestedAt':now,'receivedAt':now,'httpStatus':None,'elapsedMs':0,'rawSha256':None,'rawBytes':0,'headers':{},'errors':['VALIDATION_FINITE_BUDGET_OR_RATE_STOP'],'value':None},None
   limit=min(deadline or cutoff,cutoff,datetime.now(timezone.utc)+timedelta(seconds=10))
   obs,raw=original_attempt(source,symbol,url,parser,deadline=limit)
   item={k:obs.get(k) for k in ('source','symbol','httpStatus','elapsedMs','errors','rawSha256','rawBytes')};item['attempt']=attempt
   with lock:
    observations.append(item)
    if obs.get('httpStatus') in (401,403,429):stopped.add(host)
   if attempt==1 and any(c in {'NETWORK_TIMEOUT','NETWORK_CONNECTION_FAILED','NETWORK_ERROR'} for c in obs.get('errors',[])):continue
   return obs,raw
 def measured(name,fn):
  def call(*args,**kwargs):
   t=time.monotonic();cpu=time.process_time();event={'stage':name}
   try:
    result=fn(*args,**kwargs);event['status']='returned'
    if name=='reviewed-pool-collection':
     event.update(expectedPool=len(result['catalog']['instruments']),acceptedHistories={s:len(h['raw']['bars']) for s,h in result['histories'].items() if h},rejected={s:q.get('codes',[]) for s,q in result['quality'].items() if not result['histories'].get(s)})
    return result
   except Exception as error:event.update(status='raised',code=safe_code(error));raise
   finally:event.update(seconds=round(time.monotonic()-t,3),cpuSeconds=round(time.process_time()-cpu,3));summary['stages'].append(event)
  return call
 original_build=engine.build_analysis;original_write=engine.write_json;original_v2=v2_history.build
 def labelled_build(*args,**kwargs):
  result=original_build(*args,**kwargs);result['testData']=True;result['validationScope']={'account':'fictional-only','market':'real-public','scope':'original-cold-engine-no-live-certification'};return result
 def labelled_write(path,value):
  if Path(path).name=='report.json':value['accountMode']='fictional-validation-only'
  return original_write(path,value)
 def history_views(report,root,**kwargs):return original_v2(report,root,persist=False)
 def never(*args,**kwargs):raise AssertionError('NOTIFICATION_OR_TRADE_FORBIDDEN')
 with tempfile.TemporaryDirectory(prefix='goose-real-cold-') as folder:
  root=Path(folder)
  assert not (root/'investment/work/radar').exists()
  for symbol,broker,qty in [('600900.SH','华宝证券',100),('515450.SH','银河证券',200)]:
   preview=holdings.parse_manual([{'symbol':symbol,'quantity':qty,'costPrice':'1.2345'}],clock.astimezone(market.BJ).date().isoformat(),broker,available_cash='1000')
   holdings.commit_preview(preview,root/'investment/work/private/holdings',confirmed=True)
  with ExitStack() as stack:
   for module in (market,radar_market,market_scan,market_scan_history):stack.enter_context(patch.object(module,'_attempt',side_effect=bounded))
   for module,name,label in [(radar,'collect','reviewed-pool-collection'),(radar,'build','reviewed-pool-research'),(market,'collect_market','holdings-market-collection'),(market,'collect_quotes','final-quote-refresh'),(strategy_lab,'run_suite','five-strategy-suite')]:
    stack.enter_context(patch.object(module,name,side_effect=measured(label,getattr(module,name))))
   stack.enter_context(patch.object(engine,'build_analysis',side_effect=labelled_build))
   stack.enter_context(patch.object(engine,'write_json',side_effect=labelled_write))
   stack.enter_context(patch.object(v2_history,'build',side_effect=history_views))
   stack.enter_context(patch('investment.delivery.send_daily',side_effect=never))
   try:
    # This executes the same bounded prewarm stages as ExistingEngine.analyze,
    # without calling/bypassing its production time gate. Benchmark only.
    phase=datetime.now(timezone.utc);discovery_deadline=min(phase+timedelta(seconds=90),cutoff-timedelta(seconds=90))
    discovery=measured('broad-discovery',market_scan.generate)(root,now=phase,deadline=discovery_deadline,refresh=True)
    summary['discovery']={'status':discovery['status'],'asOf':discovery.get('asOf'),'coverage':discovery['coverage'],'providerPaginationComplete':discovery['providerPaginationComplete'],'candidateCount':0}
    if discovery.get('items') and (discovery_deadline-datetime.now(timezone.utc)).total_seconds()>=8:
     try:
      measured('cold-benchmark-cache-prewarm',radar_market.collect)(root,now=datetime.now(timezone.utc),deadline=discovery_deadline)
     except (ValueError,OSError,KeyError):pass
    if (discovery_deadline-datetime.now(timezone.utc)).total_seconds()>=8:
     review=measured('discovery-history-review',market_scan_history.generate)(root,discovery,deadline=discovery_deadline)
     summary['discoveryHistory']={k:review.get(k) for k in ('sampleCount','sampleLimit','codes','tradeCandidateCount')}
    report=measured('original-engine-report',engine.run_analysis)(root,deadline=cutoff)
    paths=list((root/'investment/work/runs').glob('*/report.json'));assert len(paths)==1,'REPORT_FILE_COUNT'
    raw=paths[0].read_bytes();saved=json.loads(raw)
    assert saved==report and report['testData'] and report['accountMode']=='fictional-validation-only','REPORT_READBACK'
    assert report['realOrdersEnabled'] is False and report['notificationSent'] is False,'SIDE_EFFECT_GUARD'
    sha=hashlib.sha256(raw).hexdigest();assert hashlib.sha256(paths[0].read_bytes()).hexdigest()==sha
    items=report.get('opportunityRadar',{}).get('items',[]);labs=[x['lab'] for x in items if x.get('lab')]
    quote_now=datetime.now(timezone.utc)
    audits={x['symbol']:market.quote_audit(x.get('quote'),report.get('diagnostics',[]),now=quote_now,symbol=x['symbol']) for x in report.get('instruments',[])}
    summary['quotes']={s:{k:a.get(k) for k in ('source','sourceTime','fetchedAt','checkedAt','sourceAgeSeconds','receiptAgeSeconds','intradaySignalAllowed','status','failureCodes')} for s,a in audits.items()}
    all_live=bool(audits) and all(a.get('intradaySignalAllowed') is True for a in audits.values())
    summary['intradayQualification']={'passed':gate['originalAnalysisWindowOpen'] and all_live,'allHoldingQuotesEligible':all_live,'reason':None if gate['originalAnalysisWindowOpen'] and all_live else 'OUTSIDE_ORIGINAL_WINDOW' if not gate['originalAnalysisWindowOpen'] else 'ORIGINAL_QUOTE_GATES_NOT_PASSED'}
    summary['report']={'generated':True,'bytes':len(raw),'sha256':sha,'readbackIdentical':True,'status':report['status'],'marketHealth':report.get('marketHealth'),'researchPoolItems':len(items),'scoredItems':sum(bool(x.get('score')) for x in items),'strategyStudies':sum(len(x.get('strategies',{})) for x in labs),'expectedMaximumStrategyStudies':len(radar_market.catalog()['instruments'])*len(strategy_lab.STRATEGIES),'labsCompleted':len(labs),'labOrQualityCodes':{x['symbol']:x.get('quality',{}).get('codes',[])+x.get('rejections',[]) for x in items if x.get('quality',{}).get('codes') or any('策略证据或计算' in r for r in x.get('rejections',[]))},'fullMarketScanVerified':False,'syntheticAuthoritativeHistoryPersisted':False}
    summary['status']='benchmark-completed'
   except Exception as error:summary.update(status='benchmark-failed',code=safe_code(error))
 summary['requests']=observations;summary['networkRequests']=requests;summary['httpStatusCounts']=dict(Counter(str(x['httpStatus']) for x in observations));summary['elapsedSeconds']=round(time.monotonic()-start,3);summary['peakRssKiB']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;summary['finishedAt']=datetime.now(timezone.utc).isoformat()
 print('GOOSE_FULL_RESULT='+json.dumps(summary,ensure_ascii=True,separators=(',',':')))
 return 0 if summary['status']=='benchmark-completed' else 2
if __name__=='__main__':raise SystemExit(main())
