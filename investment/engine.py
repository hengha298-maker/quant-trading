"""Immutable portfolio research, keeping facts, models and advice separate."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D, ROUND_HALF_UP, localcontext
from pathlib import Path
import json, math, os, re, sys, uuid
ROOT=Path(__file__).resolve().parents[1]
for p in [ROOT/'v32',ROOT/'v33']:
 if str(p) not in sys.path:sys.path.insert(0,str(p))
BJ=timezone(timedelta(hours=8))
DEFAULT_SYMBOLS=['600900.SH','515450.SH']
NAMES={'600900.SH':'长江电力','515450.SH':'红利低波ETF'}
LIMITS=['公开网页行情无稳定服务承诺，两个网站不等于两个独立交易所数据源。','本报告不连接券商、不提交订单、不发送通知。','未获取逐次分红、税款与再投资现金流；价格回测不是完整含息总回报，不据此评价长江电力长期投资收益。','真实费率尚未提供，回测手续费、滑点和印花税均为显式研究假设。']
def money(n):return format(D(n).quantize(D('.01'),rounding=ROUND_HALF_UP),'f')
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
 names=[a.get('source',{}).get('broker','用户导出') for a in accounts]
 missing=[name for name in ['华宝证券','银河证券'] if name not in names]
 positions=[]
 for a in accounts:
  for p in a['positions']:positions.append({**p,'broker':a['source']['broker'],'holdingAsOf':a['asOf']})
 cash=None if missing or any(a.get('availableCash') is None for a in accounts) else money(sum(D(a['availableCash']) for a in accounts))
 return {'asOf':min(a['asOf'] for a in accounts),'positions':positions,'availableCash':cash,'accounts':accounts,'missingBrokers':missing}

def metrics(dataset, day):
 """Model input is exactly the prior complete session; never today's bar."""
 from goose_research.market import xshg_sessions
 if not dataset or dataset.get('quality',{}).get('status')!='validated':return None
 sessions=xshg_sessions((datetime.fromisoformat(day)-timedelta(days=45)).date().isoformat(),day)
 previous=[x for x in sessions if x<day][-1]
 rows=[x for x in dataset['bars'] if x['date']<=previous]
 if len(rows)<21 or rows[-1]['date']!=previous:return None
 # Strict continuity; gaps do not become implicit complete 20-session windows.
 if [r['date'] for r in rows[-21:]]!=xshg_sessions(rows[-21]['date'],previous):return None
 close=[D(x['close']) for x in rows]; ma=sum(close[-20:])/20; prevma=sum(close[-21:-1])/20
 v1='above' if close[-1]>ma else 'below' if close[-1]<ma else 'equal'
 cross='up' if close[-2]<=prevma and close[-1]>ma else 'down' if close[-2]>=prevma and close[-1]<ma else 'none'
 peak=close[0];draw=D(0)
 for c in close:peak=max(peak,c);draw=min(draw,c/peak-1)
 returns=[float(close[i]/close[i-1]-1) for i in range(1,len(close))]
 mean=sum(returns)/len(returns);vol=D(str(math.sqrt(sum((x-mean)**2 for x in returns)/max(1,len(returns)-1))*math.sqrt(252)*100))
 volumes=[D(x['volume']) for x in rows[-20:]];base=sum(volumes[:-1])/19
 return {'through':previous,'previousClose':str(close[-1]),'ma20':format(ma,'.6f'),'v1Comparison':v1,'cross':cross,'maxPriceDrawdownPct':format(draw*100,'.2f'),'annualizedPriceVolatilityPct':format(vol,'.2f'),'latestVsPrior19VolumeRatio':format(volumes[-1]/base,'.2f') if base else None,'returnBasis':'unadjusted-price-only-dividend-events-not-accounted','corporateActionCaution':True}

def portfolio(holdings,quotes,day):
 if not holdings:return {'status':'missing','asOf':None,'positions':[],'availableCash':None,'marketValue':None,'pnl':None,'concentration':None,'warnings':['未导入当前真实持仓。历史记录没有被当作当前账户。']}
 stamp=str(holdings.get('asOf',''));current=stamp[:10]==day and not holdings.get('missingBrokers')
 positions=[];total=D(0);costtotal=D(0);allquotes=True;allcost=True
 for p in holdings['positions']:
  q=quotes.get(p['symbol']);valid=q and q.get('quality',{}).get('status') in {'intraday-verified','closing-snapshot'} and q.get('sourceTimestamp','')[:10]==day and q.get('quality',{}).get('unitVerified') is True
  qty=D(str(p['quantity']));value=qty*D(q['price']) if valid else None
  cost=D(p['costPrice'])*qty if p.get('costPrice') is not None else None
  if value is None:allquotes=False
  else:total+=value
  if cost is None:allcost=False
  else:costtotal+=cost
  positions.append({**p,'quote':q,'marketValue':money(value) if value is not None else None,'pnl':money(value-cost) if value is not None and cost is not None else None,'weightPct':None,'currentHoldingConfirmed':str(p.get('holdingAsOf',stamp))[:10]==day})
 if allquotes and total>0:
  for p in positions:p['weightPct']=format(D(p['marketValue'])/total*100,'.2f')
 by_symbol={}
 if allquotes and total>0:
  for p in positions:by_symbol[p['symbol']]=by_symbol.get(p['symbol'],D(0))+D(p['marketValue'])
 concentration=max([value/total*100 for value in by_symbol.values()],default=None)
 warnings=[]
 if stamp[:10]!=day:warnings.append('持仓日期不是今天；以下仅为旧快照按报价重估，不能称为当前真实持仓。')
 if not allquotes:warnings.append('部分价格缺失或单位未核验，总市值保持未知。')
 if not allcost:warnings.append('部分成本未知，总浮盈亏保持未知；券商成本口径可能含历史分红/费用。')
 if holdings.get('missingBrokers'):warnings.append('尚未提供的券商：'+'、'.join(holdings['missingBrokers'])+'；账户总体仓位和现金并不完整。')
 if concentration is not None and concentration>D(40):warnings.append('单一标的占已导入证券市值超过40%，需研究集中度；该阈值为可解释研究假设。')
 return {'status':'current-user-confirmed' if current else 'partial-accounts' if holdings.get('missingBrokers') else 'stale-snapshot','asOf':stamp,'positions':positions,'availableCash':holdings.get('availableCash'),'marketValue':money(total) if allquotes else None,'pnl':money(total-costtotal) if allquotes and allcost else None,'concentration':str(concentration) if concentration is not None else None,'weightBasis':'only-imported-securities-excludes-cash-and-other-assets','accounts':[{'broker':a['source']['broker'],'asOf':a['asOf'],'availableCash':a.get('availableCash'),'sourceKind':a['source']['kind']} for a in holdings.get('accounts',[])],'missingBrokers':holdings.get('missingBrokers',[]),'warnings':warnings}

def build_analysis(market,holdings,*,now=None):
 from goose_research.backtest import run_comparison
 clock=now or datetime.now(timezone.utc);day=clock.astimezone(BJ).date().isoformat();quotes=market.get('quotes',{})
 pf=portfolio(holdings,quotes,day);instruments=[]
 symbols=list(dict.fromkeys([*DEFAULT_SYMBOLS,*[p['symbol'] for p in (holdings or {}).get('positions',[])]]))
 for symbol in symbols:
  q=quotes.get(symbol);ds=market.get('histories',{}).get(symbol);risks=list(LIMITS);m=None;bt=None
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
  instruments.append({'symbol':symbol,'name':NAMES.get(symbol,symbol),'quote':q,'signal':signal,'stance':stance,'basis':basis,'risks':risks,'metrics':m,'backtest':bt,'quality':'low'})
 valid_dates=[x['quote']['sourceTimestamp'][:10] for x in instruments if x['quote']]
 return {'format':'goose-live-research','version':1,'id':str(uuid.uuid4()),'generatedAt':clock.isoformat(),'asOf':min(valid_dates) if valid_dates else day,'status':'portfolio-research' if pf['status']=='current-user-confirmed' else 'holdings-required','summary':'今天观察，不必操作。'+('已结合用户确认的当日持仓。' if pf['status']=='current-user-confirmed' else '缺少当日确认持仓，不能形成当前账户分析。'),'warnings':list(dict.fromkeys([*pf['warnings'],*LIMITS])),'portfolio':pf,'instruments':instruments,'diagnostics':market.get('diagnostics',[]),'marketContext':market.get('context',{}),'notificationSent':False,'scheduleEnabled':False,'realOrdersEnabled':False}

def compatible_report(report):
 return {'format':'goose-investment-report','version':1,'id':report['id'],'source':'养鹅本机整合研究 / 腾讯及东方财富独立请求','asOf':report['asOf'],'generatedAt':datetime.fromisoformat(report['generatedAt']).astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),'strategy':'原V1前收盘对MA20 / 真实持仓只读研究','symbols':[i['symbol'] for i in report['instruments']],'signals':[{'symbol':i['symbol'],'stance':i['stance'],'basis':i['basis'][:4000],'risk':'；'.join(i['risks'])[:4000],'quality':i['quality']} for i in report['instruments']],'backtest':None,'paperTrades':[],'notes':(report['summary']+'；'+'；'.join(report['warnings']))[:10000]}

def markdown(r):
 lines=['# 养鹅投资研究报告','',f"数据日期：{r['asOf']}；生成时间：{r['generatedAt']}",r['summary'],'','## 账户事实',f"持仓状态：{r['portfolio']['status']}；日期：{r['portfolio']['asOf'] or '未提供'}；可用资金：{r['portfolio']['availableCash'] or '未知'}"]
 for i in r['instruments']:
  lines+=['',f"## {i['name']} {i['symbol']}",f"研究判断：{i['signal']}",i['basis'],'风险：'+'；'.join(i['risks'])]
  if i['metrics']:lines.append('趋势指标：'+json.dumps(i['metrics'],ensure_ascii=False))
  if i['backtest']:
   lines+=['','### 同区间价格回测（不是含息总回报）',f"{i['backtest']['from']} 至 {i['backtest']['to']}",'|策略|价格路径收益bps|最大回撤bps|交易次数|费用分|','|---|---:|---:|---:|---:|']
   for x in i['backtest']['comparison']:lines.append(f"|{x['strategy']}|{x['totalReturnBps']}|{x['maxDrawdownBps']}|{x['tradeCount']}|{x['cost']}|")
 lines+=['','## 边界说明',*['- '+w for w in r['warnings']],'','未发送Bark；未启用自动任务；未连接券商。']
 return '\n'.join(lines)+'\n'

def run_analysis(root=None):
 from .market import collect_market
 w=work(root);lock=w/'.analysis.lock'
 try:fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 except FileExistsError:raise ValueError('ANALYSIS_ALREADY_RUNNING_OR_INTERRUPTED_CHECK_LOCK') from None
 try:
  os.close(fd)
  folder=w/'runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True,exist_ok=False)
  holdings=latest_holdings(root);symbols=list(dict.fromkeys(DEFAULT_SYMBOLS+[p['symbol'] for p in (holdings or {}).get('positions',[])]))
  try:market=collect_market(symbols,folder/'market')
  except (ValueError,OSError):market={'quotes':{},'histories':{},'diagnostics':[{'errors':['MARKET_COLLECTION_FAILED_NO_OLD_SIGNAL_REUSE']} ]}
  report=build_analysis(market,holdings)
  healthy=sum(bool(q and q.get('quality',{}).get('status') in {'closing-snapshot','intraday-verified'}) for q in market.get('quotes',{}).values())
  report['marketHealth']={'usableCount':healthy,'requestedCount':len(symbols),'status':'dated-observations' if healthy==len(symbols) else 'partial' if healthy else 'unavailable'}
  if not healthy:
   report['status']='market-unavailable';report['summary']='本次行情未通过核验，观察、不操作。没有沿用旧行情或旧信号。'
  write_json(folder/'report.json',report);write_json(folder/'goose-report-v31.json',compatible_report(report));(folder/'report.md').write_text(markdown(report),encoding='utf8')
  return report
 finally:lock.unlink()

def mark_report_freshness(report, *, now=None):
 from copy import deepcopy
 result=deepcopy(report);day=(now or datetime.now(timezone.utc)).astimezone(BJ).date().isoformat()
 try:generated=datetime.fromisoformat(report['generatedAt']).astimezone(BJ).date().isoformat()
 except (ValueError,KeyError):generated=None
 result['cachedReport']=True
 clock=now or datetime.now(timezone.utc)
 for item in result.get('instruments',[]):
  quote=item.get('quote')
  if quote and quote.get('quality',{}).get('status')=='intraday-verified':
   try:age=(clock-datetime.fromisoformat(quote['sourceTimestamp'])).total_seconds()
   except (ValueError,KeyError):age=10**9
   if age<0 or age>90:
    quote['quality']['status']='historical-snapshot';quote['quality']['intradaySignalAllowed']=False
    item['stance']='watch';item['signal']='已过期的盘中快照，观察'
    item['risks']=['报价已超过90秒，不是当前盘中信号。',*item.get('risks',[])]
 if generated!=day or report.get('asOf')!=day:
  result['status']='stale-report';result['summary']='历史报告，仅作参考。请更新行情与分析，不能沿用旧信号。'
  result['warnings']=['报告不是当日新生成的有效行情分析。',*result.get('warnings',[])]
  for item in result.get('instruments',[]):item['stance']='watch';item['signal']='历史观察，不是当前信号'
 return result

def load_latest(root=None):
 runs=work(root)/'runs'
 if runs.exists() and (runs.is_symlink() or runs.is_junction()):raise ValueError('OUTPUT_REPARSE_REJECTED')
 files=[]
 for folder in runs.iterdir() if runs.exists() else []:
  if not folder.is_dir() or folder.is_symlink() or folder.is_junction():continue
  p=folder/'report.json'
  if p.is_file() and not p.is_symlink() and not p.is_junction() and p.stat().st_size<4*1024*1024:files.append(p)
 files.sort(key=lambda p:p.stat().st_mtime_ns,reverse=True)
 return mark_report_freshness(json.loads(files[0].read_text(encoding='utf8'))) if files else None
