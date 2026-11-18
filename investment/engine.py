"""Immutable portfolio research, keeping facts, models and advice separate."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D, ROUND_HALF_UP, localcontext
from pathlib import Path
import json, os, re, sys, uuid
ROOT=Path(__file__).resolve().parents[1]
for p in [ROOT/'v32',ROOT/'v33']:
 if str(p) not in sys.path:sys.path.insert(0,str(p))
BJ=timezone(timedelta(hours=8))
DEFAULT_SYMBOLS=['600900.SH','515450.SH']
NAMES={'600900.SH':'长江电力','515450.SH':'红利低波ETF'}
LIMITS=['公开网页行情无稳定服务承诺，两个网站不等于两个独立交易所数据源。','本报告不连接券商、不提交订单、不发送通知。','未获取逐次分红、税款与再投资现金流；价格回测不是完整含息总回报，不据此评价长江电力长期投资收益。','真实费率尚未提供，回测手续费、滑点和印花税均为显式研究假设。']
def money(n):
 with localcontext() as context:
  context.prec=200
  return format(D(n).quantize(D('.01'),rounding=ROUND_HALF_UP),'f')
def write_json(path,value):
 with Path(path).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
def work(root=None):
 r=Path(os.path.abspath(root or ROOT)); p=r/'investment'/'work'
 for a in [p,*p.parents]:
  if a.exists() and (a.is_symlink() or a.is_junction()):raise ValueError('OUTPUT_REPARSE_REJECTED')
 p.mkdir(parents=True,exist_ok=True);return p

def latest_holdings(root=None):
 from .holdings import load_latest_by_broker
 accounts=load_latest_by_broker(work(root)/'private'/'holdings')
 if not accounts:return None
 source_accounts=accounts
 from .ledger import apply_events
 accounts=apply_events(root or ROOT,accounts)
 names=[a.get('source',{}).get('broker','用户导出') for a in accounts]
 missing=[name for name in ['华宝证券','银河证券'] if name not in names]
 positions=[]
 for a in accounts:
  for p in a['positions']:positions.append({**p,'broker':a['source']['broker'],'holdingAsOf':a['asOf']})
 cash=None if missing or any(a.get('availableCash') is None for a in accounts) else money(sum(D(a['availableCash']) for a in accounts))
 return {'asOf':min(a['asOf'] for a in accounts),'positions':positions,'availableCash':cash,'accounts':accounts,'sourceAccounts':source_accounts,'missingBrokers':missing}

def metrics(dataset, day):
 from .indicators import previous_session_metrics
 return previous_session_metrics(dataset, day)

def portfolio(holdings, quotes, day):
 from .accounting import build_portfolio
 return build_portfolio(holdings, quotes, day)

def account_evidence_digest(root):
 import hashlib
 from .holdings import load_latest_by_broker
 from .ledger import load_batches
 from .statements import load as load_statements
 accounts=load_latest_by_broker(work(root)/'private/holdings');batches=load_batches(root)
 evidence={'snapshots':[(a.get('snapshotId'),a.get('sourceSha256')) for a in accounts],'activeBatches':sorted((b['batchId'],b['sha256']) for b in batches),'statements':sorted((s['statementId'],s['sha256']) for s in load_statements(root))}
 return hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def build_analysis(market,holdings,*,now=None):
 from goose_research.backtest import run_comparison
 clock=now or datetime.now(timezone.utc);day=clock.astimezone(BJ).date().isoformat();quotes=market.get('quotes',{})
 pf=portfolio(holdings,quotes,day);instruments=[]
 symbols=list(dict.fromkeys([*DEFAULT_SYMBOLS,*[p['symbol'] for p in (holdings or {}).get('positions',[])]]))
 for symbol in symbols:
  q=quotes.get(symbol);ds=market.get('histories',{}).get(symbol);risks=['本标的价格回测不是完整含息总回报；分红、税费和再投资现金流尚未核验。'];m=None;bt=None
  try:m=metrics(ds,day)
  except (ValueError,IndexError):risks.append('交易日历或连续历史不足，MA20未计算。')
  if not m:risks.append('缺少截至上一交易日的有效连续日线，不输出MA20结论。')
  if ds and ds.get('quality',{}).get('status')=='validated':
   try:
    result=run_comparison(ds);bt={'from':result['from'],'to':result['to'],'comparison':[{'strategy':name,**r['metrics']} for name,r in result['strategies'].items()],'config':result['config'],'limitations':result['limitations'],'basis':'price-only-not-total-return','completeTotalReturn':False}
   except ValueError:risks.append('日线未满足回测质量要求，回测不可用。')
  adjusted=market.get('adjustedHistories',{}).get(symbol)
  if m and adjusted and adjusted.get('symbol')==symbol and adjusted.get('adjustment')=='qfq' and adjusted.get('quality',{}).get('status')=='validated-basis-only' and adjusted.get('quality',{}).get('ohlcAndCalendarValidated') is True:
   rows=[x for x in adjusted['bars'] if x['date']<=m['through']]
   if len(rows)>=20 and rows[-1]['date']==m['through'] and [x['date'] for x in rows[-20:]]==[x['date'] for x in ds['bars'] if x['date']<=m['through']][-20:]:
    qclose=D(rows[-1]['close']);qma=sum(D(x['close']) for x in rows[-20:])/20
    m['qfqContext']={'through':rows[-1]['date'],'previousClose':str(qclose),'ma20':format(qma,'.6f'),'comparison':'above' if qclose>qma else 'below' if qclose<qma else 'equal','basis':'provider-qfq-separate-not-total-return','totalReturnVerified':False}
  from .indicators import enrich_complete_session
  m=enrich_complete_session(m,ds,adjusted,clock) if m and ds else m
  live=False
  if q and q.get('quality',{}).get('status')=='intraday-verified':
   try:
    stamp=datetime.fromisoformat(q['sourceTimestamp']);age=(clock-stamp).total_seconds()
    live=0<=age<=90 and stamp.astimezone(BJ).date().isoformat()==day and q['quality'].get('unitVerified') is True
   except (ValueError,KeyError,TypeError):pass
  basis='盘中行情未验证，观察、不操作。'
  if q:basis=f"{q['source']} 来源时间{q['sourceTimestamp']}，价格{q['price']}元；"+('本次盘中字段/新鲜度通过，非持续可靠性保证。' if live else '仅带日期的收盘/历史观察，不是当前盘中信号。')
  if m:basis+=f" 原V1规则：{m['through']}前收盘{m['previousClose']}，含该日MA20={m['ma20']}，位置{m['v1Comparison']}；独立交叉对照={m['cross']}。"
  if m and m.get('qfqContext'):
   a=m['qfqContext'];basis+=f" 单独前复权趋势：前收盘{a['previousClose']}，MA20={a['ma20']}，{a['comparison']}；前复权是供应商调整序列，不是已核验含息现金流回测。"
  stance='watch';signal='观察、不操作'
  if live and pf['status']=='current-user-confirmed' and m and m['v1Comparison']=='below' and not m['corporateActionCaution']:stance='reduce';signal='研究分批减仓，先核对除权与集中度'
  elif live and pf['status']=='current-user-confirmed' and m and m['v1Comparison']=='above' and not m['corporateActionCaution']:stance='hold';signal='继续持有观察，无需机械追买'
  if not holdings:risks.insert(0,'尚无当前真实持仓，以下是关注标的研究，不能声称分析了真实账户。')
  from .scenarios import build_scenarios
  scenario=build_scenarios(symbol,q,m,pf,now=clock,dataset=ds)
  from .research import build_research
  extended=build_research(symbol,ds,adjusted,pf,clock)
  if stance!='watch':
   directional=next((s for s in scenario['scenarios'] if s['id']==stance),None)
   if not scenario['evidenceQuality']['currentIntradayVerified'] or not directional or directional['status'] not in {'conditional-fit','conditional'}:
    stance='watch';signal='观察、不操作；方向研究条件未通过'
  from .market import quote_audit
  instruments.append({'research':extended,'scenarioAnalysis':scenario,'symbol':symbol,'name':NAMES.get(symbol,symbol),'quote':q,'quoteAudit':quote_audit(q,market.get('diagnostics',[]),now=clock,symbol=symbol),'signal':signal,'stance':stance,'basis':basis,'risks':risks,'metrics':m,'backtest':bt,'quality':'low'})
 valid_dates=[x['quote']['sourceTimestamp'][:10] for x in instruments if x['quote']]
 mode='intraday' if any(i['quote'] and i['quote'].get('quality',{}).get('status')=='intraday-verified' for i in instruments) else 'closing-snapshot' if any(i['quote'] and i['quote'].get('quality',{}).get('status')=='closing-snapshot' for i in instruments) else 'historical-or-blocked'
 return {'dataMode':mode,'format':'goose-live-research','version':1,'id':str(uuid.uuid4()),'generatedAt':clock.isoformat(),'asOf':min(valid_dates) if valid_dates else day,'status':'portfolio-research' if pf['status']=='current-user-confirmed' else 'holdings-required','summary':('收盘后研究：观察，不操作。' if mode=='closing-snapshot' else '本次研究：观察，不必操作。')+('已结合用户确认的当日持仓。' if pf['status']=='current-user-confirmed' else '缺少当日确认持仓，不能形成当前账户分析。'),'warnings':list(dict.fromkeys([*pf['warnings'],*LIMITS])),'portfolio':pf,'instruments':instruments,'diagnostics':market.get('diagnostics',[]),'marketContext':market.get('context',{}),'notificationSent':False,'scheduleEnabled':None,'scheduleStateSource':'runtime-status-api','realOrdersEnabled':False}

def compatible_report(report):
 return {'format':'goose-investment-report','version':1,'id':report['id'],'source':'养鹅本机整合研究 / 腾讯及东方财富独立请求','asOf':report['asOf'],'generatedAt':datetime.fromisoformat(report['generatedAt']).astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),'strategy':'原V1前收盘对MA20 / 真实持仓只读研究','symbols':[i['symbol'] for i in report['instruments']],'signals':[{'symbol':i['symbol'],'stance':i['stance'],'basis':(i['basis']+'；'+i.get('scenarioAnalysis',{}).get('summary',''))[:4000],'risk':'；'.join(i['risks'])[:4000],'quality':i['quality']} for i in report['instruments']],'backtest':None,'paperTrades':[],'notes':(report['summary']+'；'+'；'.join(report['warnings']))[:10000]}

def markdown(r):
 lines=['# 养鹅投资研究报告','',f"数据日期：{r['asOf']}；生成时间：{r['generatedAt']}",r['summary'],'','暂无通过核验的交易候选。研究评分不代表买卖指令。','## 账户事实',f"持仓状态：{r['portfolio']['status']}；日期：{r['portfolio']['asOf'] or '未提供'}；快照可用资金（非实时、待对账）：{r['portfolio']['availableCash'] if r['portfolio']['availableCash'] is not None else '未核验'}"]
 for p in r['portfolio'].get('positions',[]):lines.append(f"- {p['symbol']} 数量{p['quantity']}，显示成本{p.get('costPrice') if p.get('costPrice') is not None else '未核验'}；持仓来源日期{p.get('holdingAsOf') or r['portfolio']['asOf']}。")
 for i in r['instruments']:
  lines+=['',f"## {i['name']} {i['symbol']}",f"研究判断：{i['signal']}",i['basis'],'风险：'+'；'.join(i['risks'])]
  if i.get('scenarioAnalysis'):
   lines += ['',i['scenarioAnalysis']['summary']]
   for scenario in i['scenarioAnalysis']['scenarios']:lines += [f"- {scenario['label']}（{scenario['status']}）：{scenario['trigger']}"]
  if i['metrics']:lines.append('趋势指标：'+json.dumps(i['metrics'],ensure_ascii=False))
  if i.get('research'):
   lines+=['','### 扩展趋势与风险研究（非已验证实盘策略）']
   for plugin in i['research']['plugins']:
    lines += [plugin['summary'],plugin.get('trigger',''),plugin.get('invalidation','')]
  if i['backtest']:
   lines+=['','### 同区间价格回测（不是含息总回报）',f"{i['backtest']['from']} 至 {i['backtest']['to']}",'|策略|价格路径收益bps|最大回撤bps|交易次数|费用分|','|---|---:|---:|---:|---:|']
   for x in i['backtest']['comparison']:lines.append(f"|{x['strategy']}|{x['totalReturnBps']}|{x['maxDrawdownBps']}|{x['tradeCount']}|{x['cost']}|")
 radar=r.get('opportunityRadar')
 if radar:
  lines+=['','## 投资机会雷达',f"完整交易日：{radar.get('through') or '未核验'}；状态：{radar['status']}；研究排序不代表未来收益概率。"]
  for kind,label in [('etf','ETF'),('stock','A股')]:
   group=radar.get('groups',{}).get(kind,{})
   lines+=['',f'### {label}',group.get('message','暂无符合条件的机会')]
   for candidate in group.get('candidates',[]):
    lines+=[f"- {candidate['name']} {candidate['symbol']}：研究分{candidate['score']['value']}/100；"+'；'.join(candidate['reasons']),f"  关注条件：{candidate['trigger']}",f"  失效条件：{candidate['invalidation']}"]
 if r.get('researchStates'):
  from .research_state import markdown as research_markdown
  lines+=research_markdown(r['researchStates'])
 lines+=['','## 边界说明',*['- '+w for w in r['warnings']],'','本报告生成步骤不发送Bark、不变更计划任务；实际任务和投递状态请查看运行状态。未连接券商账户。']
 return '\n'.join(lines)+'\n'

def run_analysis(root=None, *, deadline=None):
 from .market import collect_market
 if deadline is not None and (deadline.tzinfo is None or datetime.now(timezone.utc)>=deadline):raise ValueError('REPORT_DEADLINE_EXCEEDED')
 w=work(root)
 from .runtime_lock import analysis_mutex
 with analysis_mutex(w):
  folder=w/'runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True,exist_ok=False)
  start_evidence=account_evidence_digest(root or ROOT)
  holdings=latest_holdings(root);symbols=list(dict.fromkeys(DEFAULT_SYMBOLS+[p['symbol'] for p in (holdings or {}).get('positions',[])]))
  try:market=collect_market(symbols,folder/'market',deadline=deadline)
  except (ValueError,OSError):market={'quotes':{},'histories':{},'diagnostics':[{'errors':['MARKET_COLLECTION_FAILED_NO_OLD_SIGNAL_REUSE']} ]}
  report=build_analysis(market,holdings)
  from .ledger import account_views
  from .research import portfolio_risk
  report['accountLedger']=account_views(root or ROOT,(holdings or {}).get('sourceAccounts',(holdings or {}).get('accounts',[])))
  if holdings and report['status']=='holdings-required' and report['portfolio']['status']=='stale-snapshot':
   report['status']='dated-portfolio-research';report['summary']=('收盘快照研究：观察，不操作。' if report['dataMode']=='closing-snapshot' else '本次研究：观察，不必操作。')+'使用'+holdings['asOf']+'最近确认的持仓快照及明确标注的成交推演重估；未确认期间是否有其他交易，不能称为券商实时账户。'
  report['portfolioRisk']=portfolio_risk(market.get('histories',{}),datetime.now(timezone.utc))
  report['accountMode']='real-snapshots-readonly-no-broker-connection'
  report['accountEvidenceDigest']=start_evidence
  from .radar import generate as generate_radar,unavailable as radar_unavailable
  report['opportunityRadar']=radar_unavailable('RADAR_NOT_RUN_NO_VALID_COLLECTION_CONTEXT')
  if market.get('format')=='goose-investment-market' and not market.get('synthetic'):
   clock=datetime.now(timezone.utc);budget=90 if deadline is None else 35
   ages=[]
   for instrument in report['instruments']:
    q=instrument.get('quote')
    if q and q.get('quality',{}).get('status')=='intraday-verified':
     try:ages.append((clock-datetime.fromisoformat(q['sourceTimestamp'])).total_seconds())
     except (ValueError,KeyError):ages.append(90)
   if ages:budget=min(budget,max(0,70-max(ages)))
   if deadline is not None:budget=min(budget,(deadline-clock).total_seconds()-10)
   if budget>=8:
    try:report['opportunityRadar']=generate_radar(root or ROOT,report['portfolio'],now=clock,deadline=clock+timedelta(seconds=budget))
    except (ValueError,OSError,KeyError):report['opportunityRadar']=radar_unavailable('RADAR_COLLECTION_OR_RESEARCH_FAILED_NO_OLD_CANDIDATES')
   else:report['opportunityRadar']=radar_unavailable('RADAR_BUDGET_RESERVED_FOR_EXISTING_REPORT')
  # Research work must not age an otherwise current quote past the original
  # 30-second receipt gate. Refresh quotes only; reuse all validated histories.
  if deadline is None or (deadline-datetime.now(timezone.utc)).total_seconds()>12:
   from .market import collect_quotes
   try:
    tail=collect_quotes(symbols,folder/'quotes-final',deadline=deadline)
    market['quotes']=tail['quotes'];market['diagnostics']=[*market.get('diagnostics',[]),*tail.get('diagnostics',[])]
    updated=build_analysis(market,holdings)
    for key in ['dataMode','generatedAt','asOf','status','summary','portfolio','instruments','diagnostics','marketContext']:report[key]=updated[key]
    if holdings and report['portfolio']['status']=='stale-snapshot':
     report['status']='dated-portfolio-research'
     report['summary']='使用'+holdings['asOf']+'有日期的确认持仓及明确标注的成交推演重估；价格与公司行为须分别核验，不是券商实时账户。'
   except (ValueError,OSError):report['warnings'].append('最终报价刷新失败；早先带时间记录仍按接收30秒/来源90秒原门槛核验，不冒充新报价。')
  if account_evidence_digest(root or ROOT)!=start_evidence:
   report['sourceChanged']=True;report['warnings'].insert(0,'分析期间账户记录发生变化，本报告不作当前账户判断。')
  if deadline is not None and datetime.now(timezone.utc)>=deadline:
   report['deadlineMissed']=True;report['warnings'].insert(0,'报告已超过预定截止时刻，不可作为14:45前报告发送。')
  healthy=sum(bool(q and q.get('quality',{}).get('status') in {'closing-snapshot','intraday-verified'}) for q in market.get('quotes',{}).values())
  report['marketHealth']={'usableCount':healthy,'requestedCount':len(symbols),'status':'dated-observations' if healthy==len(symbols) else 'partial' if healthy else 'unavailable'}
  if not healthy:
   report['status']='market-unavailable';report['summary']='本次行情未通过核验，观察、不操作。没有沿用旧行情或旧信号。'
  from .brief import summary as decision_summary
  from .research_state import build as research_states,recover_quote_times
  recover_quote_times(report,root or ROOT)
  try:previous=load_latest(root,refresh=False)
  except (ValueError,OSError):previous=None
  report['researchStates']=research_states(report,previous)
  report['decisionBrief']=decision_summary(report)
  from .holding_decision import build as holding_decision
  for instrument in report['instruments']:
   instrument['decisionAnalysis']=holding_decision(instrument,report['portfolio'],now=datetime.now(timezone.utc),radar=report['opportunityRadar'])
  # Broad discovery is independently prewarmed by the existing report host.
  # Reading a dated cache never stalls the 14:38 pipeline on thousands of rows.
  from .market_scan import load_cached
  report['marketDiscovery']=load_cached(root or ROOT)
  from .market_scan_history import load_cached as discovery_history
  history_reviews=discovery_history(root or ROOT,report['marketDiscovery'])
  for candidate in report['marketDiscovery'].get('items',[]):
   if candidate['symbol'] in history_reviews.get('reviews',{}):candidate['historyReview']=history_reviews['reviews'][candidate['symbol']]
  from .signal_tracking import record as record_signals,view as signal_view,histories as signal_histories
  try:
   record_signals(report,root or ROOT)
   followup_histories=signal_histories(root or ROOT);followup_histories.update(market.get('histories',{}))
   report['signalTracking']=signal_view(root or ROOT,datasets=followup_histories,current_report=report)
  except (ValueError,OSError,KeyError):
   report['signalTracking']={'status':'unavailable','reason':'信号记录或同口径后续数据未通过检查；原报告保留，不虚构表现。','items':[]}
  from .v2_history import build as v2_history
  report['v2History']=v2_history(report,root or ROOT,persist=True)
  from .signal_review import enrich as enrich_signal_review
  enrich_signal_review(report,root or ROOT,persist=True)
  write_json(folder/'report.json',report);write_json(folder/'goose-report-v31.json',compatible_report(report));(folder/'report.md').write_text(markdown(report),encoding='utf8')
  return report

def mark_report_freshness(report, *, now=None, root=None):
 from copy import deepcopy
 result=deepcopy(report);day=(now or datetime.now(timezone.utc)).astimezone(BJ).date().isoformat()
 try:generated=datetime.fromisoformat(report['generatedAt']).astimezone(BJ).date().isoformat()
 except (ValueError,KeyError):generated=None
 result['cachedReport']=True
 clock=now or datetime.now(timezone.utc)
 if root is not None and report.get('accountEvidenceDigest'):
  try:changed=report['accountEvidenceDigest']!=account_evidence_digest(root)
  except (ValueError,OSError):changed=True
  if changed:
   result['status']='account-source-changed';result['summary']='账户或成交记录已变化，请生成新报告；这份报告只作历史参考。'
   result.setdefault('warnings',[]).insert(0,'持仓/成交证据指纹已改变，旧报告不得冒充新账户分析。')
   result.setdefault('portfolio',{})['status']='source-changed'
   for item in result.get('instruments',[]):item['stance']='watch';item['signal']='账户记录已变，观察等待新报告'
 for item in result.get('instruments',[]):
  quote=item.get('quote')
  if quote and quote.get('quality',{}).get('status')=='intraday-verified':
   try:age=(clock-datetime.fromisoformat(quote['sourceTimestamp'])).total_seconds()
   except (ValueError,KeyError):age=10**9
   if age<0 or age>90:
    quote['quality']['status']='historical-snapshot';quote['quality']['intradaySignalAllowed']=False
    item['stance']='watch';item['signal']='已过期的盘中快照，观察'
    item['risks']=['报价已超过90秒，不是当前盘中信号。',*item.get('risks',[])]
 # A report freshly generated on a closed exchange day can research the last
 # completed session. Keep all quote gates historical, without telling the
 # user that repeated refreshes could manufacture today's intraday data.
 closed_day_research=False
 try:
  from .radar_market import complete_session
  from goose_research.market import xshg_sessions
  closed_day_research=(generated==day and not xshg_sessions(day,day) and report.get('asOf')==complete_session(clock))
 except (ValueError,ImportError):pass
 if (generated!=day or report.get('asOf')!=day) and not closed_day_research:
  result['status']='stale-report';result['summary']='历史报告，仅作参考。请更新行情与分析，不能沿用旧信号。'
  result['warnings']=['报告不是当日新生成的有效行情分析。',*result.get('warnings',[])]
  for item in result.get('instruments',[]):item['stance']='watch';item['signal']='历史观察，不是当前信号'
 elif closed_day_research and result.get('status')!='account-source-changed':
  result['status']='closed-session-research'
  result['summary']='休市日研究：报价为'+str(report.get('asOf'))+'带日期历史快照；没有今日盘中行情，不提供实时买卖信号。日线与观察池证据见下方。'
 if generated!=day:
  result.setdefault('portfolio',{})['status']='stale-snapshot'
  result['portfolio']['valuationIsCurrent']=False
  for p in result['portfolio'].get('positions',[]):p['currentHoldingConfirmed']=False
 from .valuation import refresh_valuation_view
 refresh_valuation_view(result,clock)
 radar=result.get('opportunityRadar')
 if radar:
  from .radar_market import complete_session
  try:expected=complete_session(clock)
  except ValueError:expected=None
  if generated!=day or radar.get('through')!=expected or expected is None or result.get('status')=='account-source-changed':
   radar['status']='historical';radar['currentCandidatesAllowed']=False
   for group in radar.get('groups',{}).values():
    group['candidates']=[];group['message']='原研究日期或账户证据已变化，请更新分析；旧名单仅作历史参考。'
   for item in radar.get('items',[]):item['eligible']=False
 from .scenarios import build_scenarios
 from .market import quote_audit
 from .holding_decision import build as holding_decision
 for item in result.get('instruments',[]):
  item['quoteAudit']=quote_audit(item.get('quote'),result.get('diagnostics',[]),now=clock,symbol=item['symbol'])
  if 'scenarioAnalysis' in item:
   item['scenarioAnalysis']=build_scenarios(item['symbol'],item.get('quote'),item.get('metrics'),result.get('portfolio',{}),now=clock)
  item['decisionAnalysis']=holding_decision(item,result.get('portfolio',{}),now=clock,radar=result.get('opportunityRadar'))
 for item in (radar or {}).get('items',[]):
  quality=item.get('quality') or {}
  previous_audit=quality.get('quoteAudit') or {}
  audit=quote_audit(quality.get('quote'),now=clock,symbol=item.get('symbol'))
  audit['attempts']=previous_audit.get('attempts',[])
  quality['quoteAudit']=audit
 from .brief import summary as decision_summary
 from .research_state import view as research_view,recover_quote_times
 if root is not None:recover_quote_times(result,root)
 result['researchStates']=research_view(result)
 result['decisionBrief']=decision_summary(result)
 return result

def load_latest(root=None, *, refresh=True):
 runs=work(root)/'runs'
 if runs.exists() and (runs.is_symlink() or runs.is_junction()):raise ValueError('OUTPUT_REPARSE_REJECTED')
 files=[]
 for folder in runs.iterdir() if runs.exists() else []:
  if not folder.is_dir() or folder.is_symlink() or folder.is_junction():continue
  p=folder/'report.json'
  if p.is_file() and not p.is_symlink() and not p.is_junction() and p.stat().st_size<4*1024*1024:files.append(p)
 files.sort(key=lambda p:p.stat().st_mtime_ns,reverse=True)
 if not files:return None
 report=json.loads(files[0].read_text(encoding='utf8'))
 return mark_report_freshness(report,root=root or ROOT) if refresh else report
