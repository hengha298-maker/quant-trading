"""Read-only explanation of the existing radar, not another signal strategy.

The original rejection results are authoritative: do not re-evaluate rounded
display features against thresholds. Near means only the existing 5-point
breakout-position band, never an eased admission rule. No complete trading
admission engine exists in this project; research eligibility cannot replace it.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
import hashlib, json, re
from .radar_market import catalog

REVISION = 2
LABELS = {'insufficient':'数据不足', 'watch':'继续观察', 'near':'条件接近', 'admitted':'完整准入通过'}
# Exact existing rejection messages from radar.build; numeric thresholds remain
# exclusively evaluated there, using unrounded Decimal features.
RULES = (
 ('score','研究分门槛','研究分不足65。','固定研究分至少65，分数不是胜率。'),
 ('trend','完整日趋势','完整日趋势未形成收盘高于MA20高于MA60。','完整日收盘高于同口径MA20，且MA20高于MA60。'),
 ('momentum','动量与相对强弱','20日未复权价格动量或60日双方未复权相对强弱未满足。','20日标的未复权价格动量为正，60日双方未复权相对沪深300ETF价格代理强弱不为负。'),
 ('risk','波动与回撤','波动或回撤超过研究门槛。','ETF：126交易日回撤不超过25%、60日年化价格波动不超过40%；股票分别为35%、50%。'),
 ('liquidity','历史流动性','估算历史成交额不足，不保证可成交。','20日收盘价×股/份量估算日成交额：ETF至少5000万元、股票至少1亿元；不是实际成交额。'),
 ('basis','价格口径','原始价与前复权趋势方向冲突，需核对企业行为。','原始价与前复权趋势方向一致；不把前复权收益当作含息回报。'),
 ('correlation','持仓相关性','持仓相关性证据不完整，组合适配待核对。','已有持仓的60共同交易日相关性证据齐全。'),
 ('new-position','新增机会身份','已在确认持仓中，列入持仓研究，不冒充新增机会。','本标的不在确认持仓中；已有持仓只作持仓研究，不计新增机会。'),
 ('overlap','重复暴露','现有证券快照较集中且同向相关性高，优先核对重复暴露。','未同时触发“集中度至少50%且最大相关性至少0.90”的重复暴露拦截。'),
 ('peer-coverage','同类覆盖','同类有效样本不足，拒绝生成不可靠排名。','ETF至少5只、股票至少4只有效可比较样本。'),
)
LEGACY_MOMENTUM_REJECTION = '短期动量或相对市场强弱未满足。'
LEGACY_MOMENTUM_CONDITION = '20日动量为正，60日相对沪深300ETF价格代理强弱不为负（历史旧口径，未按新版重算）。'
DATA_CODES = {
 'RADAR_ETF_SCALE_OR_LARGE_MOVE_REVIEW':'ETF价格尺度或大幅变化尚未解除隔离',
 'RADAR_QFQ_HISTORY_FAILED':'前复权日线未可靠取得',
 'RADAR_NONE_HISTORY_FAILED':'未复权日线未可靠取得',
 'RADAR_HISTORY_GAP':'日线交易日不连续',
 'RADAR_BUDGET_RESERVED_FOR_EXISTING_REPORT':'为持仓报告保留时间，本次未完成观察池采集',
 'RADAR_COLLECTION_OR_RESEARCH_FAILED_NO_OLD_CANDIDATES':'本次观察池采集或研究失败，未沿用旧名单',
}
TRADE_GAPS = [
 '完整交易准入尚未实现；现有研究分和观察门槛不能代替交易准入。',
 '资金流、估值与个人风险预算未完整核验。',
 '历史价格版本及观察池点时成员未认证，尚无前瞻实盘验证。',
]
INVALIDATION = '跌回MA20、60日相对动量转负、异常复权/单位/缺失日线，或账户证据改变时，原观察条件失效。'

def recover_quote_times(report,root):
 """Recover only dated quote timestamps from the original hashed response.

 Never clears a history quarantine or upgrades a quote to intraday-verified.
 Require the same response hash and receipt time as this radar's saved quotes.
 """
 radar=report.get('opportunityRadar') or {}
 try:
  references=[i['quality']['quote'] for i in radar.get('items',[]) if (i.get('quality') or {}).get('quote')]
  if not references:return
  reference=references[0];stamp=reference['fetchedAt'];digest=reference['rawSha256']
  if any(q.get('fetchedAt')!=stamp or q.get('rawSha256')!=digest for q in references):return
  received=datetime.fromisoformat(stamp)
  if received.tzinfo is None:return
  prefix=received.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'
  base=Path(root)/'investment/work/radar/observations'
  from .holdings import _safe_path
  from .radar_market import parse_quotes
  folders=list(_safe_path(base).glob(prefix+'*'))
  if len(folders)!=1:return
  folder=_safe_path(folders[0]);p=_safe_path(folder/'collection.json',file=True)
  if p.stat().st_size>1024*1024:return
  collection=json.loads(p.read_text(encoding='utf8'))
  if collection['through']!=radar['through']:return
  obs=next(o for o in collection['observations'] if o.get('source')=='tencent-radar-quotes' and o.get('httpStatus')==200 and not o.get('errors'))
  if obs.get('rawSha256')!=digest or obs.get('receivedAt')!=stamp:return
  if not re.fullmatch(r'pool-tencent-radar-quotes-[a-f0-9]{6}\.raw',obs['rawFile']):return
  rawpath=_safe_path(folder/obs['rawFile'],file=True)
  if rawpath.stat().st_size>65536:return
  raw=rawpath.read_bytes()
  if hashlib.sha256(raw).hexdigest()!=obs['rawSha256']:return
  parsed=parse_quotes(raw,{s['symbol']:s for s in catalog()['instruments']})
  radar['quoteTimeEvidence']={symbol:{'sourceTimestamp':q['sourceTimestamp'],
   'source':'tencent-quote','rawSha256':obs['rawSha256'],'rawFile':str(rawpath.relative_to(Path(root))),
   'receivedAt':obs['receivedAt'],'meaning':'原始公开报价时间字段；不解除历史隔离，不是已认证末笔成交时间'} for symbol,q in parsed['quotes'].items()}
 except (ValueError,KeyError,TypeError,OSError,StopIteration):return

def _unique(values):
 return list(dict.fromkeys(v for v in values if isinstance(v,str) and v))

def _valid_date(value):
 try:return date.fromisoformat(value).isoformat() == value
 except (TypeError,ValueError):return False

def _valid_stamp(value):
 try:return datetime.fromisoformat(value).tzinfo is not None
 except (TypeError,ValueError):return False

def _model(radar,item):
 lab=item.get('lab') or {}
 return hashlib.sha256(json.dumps({'revision':REVISION,'rules':RULES,
  'catalogVersion':radar.get('catalogVersion'),'catalogReviewedOn':radar.get('catalogReviewedOn'),
  'radarModelRevision':radar.get('modelRevision',1),'factorBasis':radar.get('factorBasis'),
  'modelRevision':lab.get('modelRevision'),'config':lab.get('config'),'partitions':lab.get('partitions'),
  'radarSourceSha256':hashlib.sha256((Path(__file__).parent/'radar.py').read_bytes()).hexdigest(),
  'source':(item.get('quality',{}).get('quote') or {}).get('source')},sort_keys=True).encode()).hexdigest()

def _history(item):
 lab=item.get('lab') or {};trust=item.get('trustResearch') or {};source=trust.get('sourceEvidence') or {}
 rows=[];base=lab.get('strategies',{}).get('buy_hold',{}).get('partitions',{}).get('test',{}).get('metrics') or {}
 for name,value in lab.get('strategies',{}).items():
  if name=='buy_hold' or value.get('applicable') is False:continue
  test=value.get('partitions',{}).get('test',{});m=test.get('metrics') or {}
  excess=m.get('totalReturnBps')-base['totalReturnBps'] if isinstance(m.get('totalReturnBps'),int) and isinstance(base.get('totalReturnBps'),int) else None
  rows.append({'strategy':name,'from':test.get('from'),'through':test.get('to'),'sessions':m.get('tradingDays'),
   'tradeCount':m.get('tradeCount'),'excessPriceReturnBps':excess})
 verified=trust.get('verifiedWindow') or {}
 return {'status':'research-only' if lab else 'unavailable',
  'label':'有固定规则历史留出，尚未证明实盘有效' if lab else '当前同口径回测不可用',
  'from':lab.get('from'),'through':lab.get('to'),'strategies':rows,
  'enoughForTrading':False,'advantage':trust.get('advantageConclusion') or '尚未证明优于买入持有',
  'completeCashflowCoverage':source.get('complete') is True,
  'verifiedWindow':{'from':verified.get('from'),'through':verified.get('to')} if verified else None,
  'limitations':_unique([*lab.get('limitations',[]),
   '次日开盘假设及成交费用已建模；成交次数不是独立样本，不能只凭收益差判断有效。',
   '前复权为事后可修订序列，不能据无未来行访问便声称点时无偏。',
   '分红、拆分、停复牌只在有来源的覆盖区间处理，当前完整区间证据不足。'])}

def _one(spec,item,radar,report):
 item=item or {};quality=item.get('quality') or {};features=item.get('features') or {};score=item.get('score') or {};lab=item.get('lab') or {}
 through=item.get('through') or radar.get('through');rejections=item.get('rejections') or []
 known={r[2] for r in RULES}|{LEGACY_MOMENTUM_REJECTION};unknown=[r for r in rejections if r not in known]
 parts={p.get('factor'):p for p in score.get('parts',[]) if isinstance(p,dict)}
 available=(quality.get('status') in {'validated-public-history','cached-complete-session'}
  and _valid_date(through) and through==radar.get('through')==features.get('signalAsOf')==lab.get('to')
  and isinstance(score.get('value'),int) and len(parts)==7 and bool(lab.get('strategies'))
  and isinstance(item.get('eligible'),bool) and item['eligible']==(not rejections)
  and not lab.get('synthetic') and not unknown)
 if report.get('synthetic') or report.get('testData') or radar.get('synthetic'):available=False
 raw_momentum=(radar.get('factorBasis') or {}).get('momentum20')=='unadjusted-subject-price' or RULES[2][2] in rejections
 rules=[{'id':key,'label':label,'condition':LEGACY_MOMENTUM_CONDITION if key=='momentum' and not raw_momentum else condition,
  'status':('failed' if rejection in rejections or key=='momentum' and LEGACY_MOMENTUM_REJECTION in rejections else 'passed') if available else 'unknown'}
  for key,label,rejection,condition in RULES]
 near=available and parts.get('突破位置',{}).get('points')==5
 # This exact band is already defined in radar.score (-2% inclusive to 0%
 # inclusive, excluding a confirmed breakout). It is explanatory, not a gate.
 proximity={'id':'breakout-position','label':'接近前20日高点','status':'near' if near else 'triggered' if available and parts.get('突破位置',{}).get('points')==10 else 'not-near' if available else 'unknown',
  'basis':parts.get('突破位置',{}).get('basis') if available else None,
  'meaning':('仅处于原评分的5分接近档位，不代表已突破，更不代表可以买入。' if near else
   '原日线评分已越过前20日高点；不代表当日盘中可买入。' if available and parts.get('突破位置',{}).get('points')==10 else
   '当前不处于原评分的接近高点档位。' if available else '当前数据不足，未判定接近或突破。')}
 state='insufficient' if not available else 'near' if near else 'watch'
 current=radar.get('currentCandidatesAllowed') is not False and radar.get('status') in {'ready','partial'} and not report.get('sourceChanged')
 if not current:state='insufficient'
 quote=quality.get('quote') or {};time_evidence=(radar.get('quoteTimeEvidence') or {}).get(spec['symbol']) or {}
 stamp=quote.get('sourceTimestamp') or time_evidence.get('sourceTimestamp')
 gaps=[DATA_CODES.get(c,'来源核验未通过：'+c) for c in quality.get('codes',[])]
 if not available:gaps.extend(unknown or ['当前完整日线、单位、复权、基准或回测证据不足，不能形成有效判断。'])
 if not current:gaps.append('研究日期或账户证据已变化，只保留注明日期的历史依据，不沿用旧状态。')
 failures=[r for r in rules if r['status']=='failed'];passed=[r for r in rules if r['status']=='passed']
 gaps.extend(rejections);trade=list(TRADE_GAPS)
 if report.get('portfolio',{}).get('status')!='current-user-confirmed' or (radar.get('accountConstraints') or {}).get('cashVerifiedCurrent') is not True:
  trade.append('账户为有日期的确认快照，现金尚待对账；不是券商实时账户。')
 if quote.get('quality',{}).get('status')!='intraday-verified' or quote.get('quality',{}).get('intradaySignalAllowed') is not True:
  trade.append('本标的未通过当日盘中报价新鲜度核验；完整日研究不能代替盘中信号。')
 history=_history(item)
 if not history['completeCashflowCoverage']:trade.append('当前完整期限的分红、拆分及停复牌覆盖未核验完整。')
 if not available:next_step='先修复并核验日线、价格单位和复权口径；不据隔离数据判断方向。'
 elif not current:next_step='等待下一次正式分析取得符合研究日期的证据，不通过反复刷新制造新信号。'
 elif near:next_step='观察下一完整交易日是否收盘高于此前20日高点（排除信号日）；同时核对其余未满足条件。'
 elif failures:next_step=failures[0]['condition']
 else:next_step=item.get('trigger') or '下一完整交易日继续核对趋势、相对强弱与数据口径；账户和风险预算另行核验。'
 return {'symbol':spec['symbol'],'name':spec['name'],'kind':spec['kind'],'state':state,'label':LABELS[state],
  'through':through if _valid_date(through) else None,'quoteSourceTime':stamp if _valid_stamp(stamp) else None,
  'quoteTimeRecovered':not bool(quote.get('sourceTimestamp')) and bool(time_evidence),'quoteTimeProvenance':time_evidence or None,
  'historyFetchedAt':quality.get('historyFetchedAt'),'dataSource':'腾讯公开日线/报价；同源口径核验，非独立交易所行情',
  'available':available,'currentResearchAllowed':current,'isHeld':item.get('isHeldInSnapshot') is True,
  'researchQualified':available and current and item.get('eligible') is True,
  'conclusion':('既有日线观察门槛通过；仍为研究，未通过完整交易准入。' if available and item.get('eligible') is True else LABELS[state]+'；'+('仅接近已有突破位置档位，其余条件仍须核对。' if near else '依据下方现有规则继续核验。' if available else '不输出方向性结论。')),
  'rules':rules,'metConditions':[r['condition'] for r in passed],
  'unmetConditions':[r['condition'] for r in failures],'dataGaps':_unique(gaps),
  'tradeAdmission':{'status':'not-available','passed':False,'gaps':trade},
  'proximity':proximity,'nextCondition':next_step,
  'ifSatisfied':'仅增加研究依据；其余研究条件、当日数据、资金和风险预算仍须核验，不等于可以买入。',
  'invalidation':item.get('invalidation') if available and item.get('invalidation') else INVALIDATION,
  'ifInvalidated':'原观察条件撤销，回到观察或数据不足；先核对原因，不自动交易。',
  'risks':_unique([*item.get('risks',[]),*radar.get('limitations',[]),*TRADE_GAPS]),
  'history':history,'modelFingerprint':_model(radar,item),
  'score':score.get('value') if available else None,
  'change':{'comparable':False,'reason':'未建立同口径较早研究日的可比记录','newlyMet':[],'invalidated':[],'stateChanged':False}}

def _compare(current,previous,report,old):
 try:
  recorded=next((i for i in old.get('researchStates',{}).get('items',[]) if i.get('symbol')==current['symbol']),{})
  valid=(report.get('accountEvidenceDigest') is not None and report.get('accountEvidenceDigest')==old.get('accountEvidenceDigest')
   and not any(r.get(k) for r in (report,old) for k in ('synthetic','testData','cachedReport','sourceChanged','deadlineMissed'))
   and current['currentResearchAllowed'] and previous['currentResearchAllowed']
   and current['modelFingerprint']==previous['modelFingerprint']
   and recorded.get('modelFingerprint')==previous['modelFingerprint']
   and _valid_date(current['through']) and _valid_date(previous['through']) and previous['through']<current['through']
   and _valid_stamp(report.get('generatedAt')) and _valid_stamp(old.get('generatedAt'))
   and datetime.fromisoformat(old['generatedAt'])<datetime.fromisoformat(report['generatedAt']))
 except (TypeError,ValueError):valid=False
 if not valid:return
 before={r['id']:r['status'] for r in previous['rules']}
 current['change']={'comparable':True,'reason':'同模型、同账户证据、较早完整研究日对比',
  'previousThrough':previous['through'],'previousLabel':previous['label'],
  'newlyMet':[r['id'] for r in current['rules'] if r['status']=='passed' and before.get(r['id'])=='failed'],
  'invalidated':[r['id'] for r in current['rules'] if r['status']=='failed' and before.get(r['id'])=='passed'],
  'stateChanged':current['state']!=previous['state']}
 if current['proximity']['status']!=previous['proximity']['status']:
  current['change']['proximityChange']={'from':previous['proximity']['status'],'to':current['proximity']['status']}

def _attention(items):
 def changed(i):
  c=i['change'];return bool(c['newlyMet'] or c['invalidated'] or c['stateChanged'] or c.get('proximityChange'))
 def order(i):
  return (not changed(i),not i['researchQualified'],i['isHeld'],{'near':0,'watch':1,'insufficient':2}.get(i['state'],3),-(i['score'] or 0),i['symbol'])
 return [i['symbol'] for i in sorted(items,key=order) if not i['isHeld'] and (i['available'] and i['currentResearchAllowed'] or changed(i))][:3]

def build(report,previous=None):
 """Pure adapter: all 16 catalog entries, with no cache writes or networking."""
 radar=report.get('opportunityRadar') or {};by_symbol={i.get('symbol'):i for i in radar.get('items',[])}
 items=[_one(s,by_symbol.get(s['symbol']),radar,report) for s in catalog()['instruments']]
 if isinstance(previous,dict):
  old_radar=previous.get('opportunityRadar') or {};old_by={i.get('symbol'):i for i in old_radar.get('items',[])}
  for spec,item in zip(catalog()['instruments'],items):
   if item['symbol'] in old_by:_compare(item,_one(spec,old_by[item['symbol']],old_radar,previous),report,previous)
 counts={key:sum(i['state']==key for i in items) for key in LABELS}
 return {'format':'goose-research-states','version':REVISION,'sourceReportId':report.get('id'),
  'analysisTime':report.get('generatedAt'),'through':radar.get('through'),'items':items,'counts':counts,
  'attentionSymbols':_attention(items),
  'tradeCandidateCount':0,'completeAdmissionAvailable':False,
  'method':'状态来自既有雷达拒绝原因与原突破位置档位；排序是研究阅读顺序，不是买入推荐。',
  'tracking':'仅比较同模型、同账户证据、较早完整研究日；同日刷新和缓存降级不当成新触发。'}

def view(report):
 """Preserve recorded changes, downgrade an expired view without rewriting it."""
 stored=report.get('researchStates');fresh=build(report)
 if not isinstance(stored,dict) or stored.get('version')!=REVISION:return fresh
 prior={i['symbol']:i for i in stored.get('items',[])}
 for item in fresh['items']:
  old=prior.get(item['symbol'])
  if old and item['currentResearchAllowed'] and old.get('modelFingerprint')==item['modelFingerprint'] and old.get('through')==item['through']:
   item['change']=deepcopy(old['change'])
 fresh['attentionSymbols']=_attention(fresh['items'])
 return fresh

def markdown(study):
 lines=['','## 16只观察池研究状态','完整准入交易候选：0。'+study['method'],study['tracking']]
 for item in study['items']:
  lines+=['',f"### {item['name']} {item['symbol']} · {item['label']}",
   f"研究日：{item['through'] or '未核验'}；公开报价源时间：{item['quoteSourceTime'] or '未取得可靠时刻'}",item['conclusion'],
   '已满足：'+'；'.join(item['metConditions']) if item['metConditions'] else '已满足：无足够证据。',
   '主要缺口：'+'；'.join([*item['dataGaps'],*item['tradeAdmission']['gaps']]),
   '下一步：'+item['nextCondition'],'条件成立：'+item['ifSatisfied'],
   '失效条件：'+item['invalidation'],'条件失效：'+item['ifInvalidated'],
   '历史验证：'+item['history']['label']+'；'+item['history']['advantage'],
   '主要风险：'+'；'.join(item['risks'])]
 return lines
