"""Small factual summary of existing research; never creates trading eligibility."""
from datetime import datetime, timezone, timedelta
import re
from .radar_market import catalog

RISK_LABELS = {
 'market-unverified': '报价未通过盘中核验',
 'account-dated': '持仓是历史确认快照，现金待对账',
 'below-ma20': '上一完整日收盘低于MA20',
 'adjustment-conflict': '原始价与复权趋势冲突',
 'evidence-incomplete': '资金、估值或历史证据未完整核验',
 'corporate-action-review': '除权除息价格口径待核验',
}
ATTENTION = '完整日保持同口径MA20/60与相对强弱，再核对当日行情、资金及风险预算。'
INVALIDATION = '跌回MA20、相对强弱转负或数据核验失败，原观察条件失效。'
REDUCE = '完整日继续弱于MA20或集中度超过个人上限时研究减仓；先核对除权、费用和可卖数量。'

def names():
 return {s['symbol']: s['name'] for s in catalog()['instruments']}

def summary(report):
 risks=['evidence-incomplete'];times=[];positions=[]
 pf=report.get('portfolio',{})
 if pf.get('status')!='current-user-confirmed':risks.append('account-dated')
 for item in report.get('instruments',[]):
  quote=item.get('quote') or {};stamp=quote.get('sourceTimestamp')
  if stamp:
   try:
    if datetime.fromisoformat(stamp).tzinfo is not None:times.append(stamp)
   except (ValueError,TypeError):pass
  if quote.get('quality',{}).get('status')!='intraday-verified':risks.append('market-unverified')
  if quote.get('corporateActionReviewRequired') or 'CORPORATE_ACTION_PRICE_BASIS_UNVERIFIED' in quote.get('quality',{}).get('codes',[]):risks.append('corporate-action-review')
  metrics=item.get('metrics') or {};scenarios=item.get('scenarioAnalysis') or {}
  if metrics.get('v1Comparison')=='below':risks.append('below-ma20')
  if scenarios.get('modelConditions',{}).get('adjustmentConflict'):risks.append('adjustment-conflict')
  positions.append({'symbol':item.get('symbol'),'trendThrough':metrics.get('through'),
   'trend':metrics.get('v1Comparison'),'riskCondition':REDUCE,'invalidation':INVALIDATION})
 radar=report.get('opportunityRadar') or {};watch=[]
 if radar.get('status') in {'ready','partial'} and radar.get('currentCandidatesAllowed') is not False:
  for kind in ('etf','stock'):
   watch.extend(c.get('symbol') for c in radar.get('groups',{}).get(kind,{}).get('candidates',[])[:2])
 return {'quoteTimes':sorted(set(times)),'riskCodes':sorted(set(risks)),
  'watchSymbols':[s for s in dict.fromkeys(watch) if s in names()][:4],
  'tradeCandidateCount':0,'tradeCandidateMessage':'暂无通过核验的交易候选',
  'attentionCondition':ATTENTION,'invalidation':INVALIDATION,'positions':positions,
  'coverage':'资金、估值与策略前瞻证据未完整接入；研究评分不代表买卖指令。'}

def comparable(report, previous):
 """Risk deltas require the same account evidence and observation regime.

 A viewer's age-downgraded copy and a weekend snapshot are not evidence of a
 newly triggered intraday risk. Missing metadata disables the comparison.
 """
 if not isinstance(previous,dict):return False
 digest=report.get('accountEvidenceDigest')
 if not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest) or previous.get('accountEvidenceDigest')!=digest:return False
 for value in (report,previous):
  if value.get('format')!='goose-live-research' or value.get('version')!=1 or value.get('dataMode')!='intraday':return False
  if any(value.get(k) for k in ('synthetic','testData','cachedReport','sourceChanged','deadlineMissed')):return False
 symbols=[{i.get('symbol') for i in r.get('instruments',[])} for r in (report,previous)]
 if not symbols[0] or symbols[0]!=symbols[1]:return False
 try:
  current=datetime.fromisoformat(report['generatedAt']);old=datetime.fromisoformat(previous['generatedAt'])
  return current.tzinfo is not None and old.tzinfo is not None and old<current
 except (KeyError,TypeError,ValueError):return False

def notification_summary(report,previous=None):
 value=summary(report);codes=value['riskCodes'];positions=value['positions']
 value={k:value[k] for k in ('quoteTimes','riskCodes','watchSymbols','tradeCandidateCount')}
 value['holdingTrends']=[{k:p[k] for k in ('symbol','trend','trendThrough')} for p in positions if p['symbol'] in names()]
 allowed=comparable(report,previous)
 value['newRiskCodes']=sorted(set(codes)-set(summary(previous)['riskCodes'])) if allowed else []
 value['comparisonAvailable']=allowed
 value['datedHoldingComparisons']=[]
 archive=report.get('v2History') or {}
 if archive.get('sourceReportId')==report.get('id') and report.get('format')=='goose-live-research' and not any(report.get(k) for k in ('synthetic','testData','cachedReport','sourceChanged')):
  for item in archive.get('holdings',[]):
   change=item.get('trendChange','');before=item.get('previousResearchThrough');through=item.get('currentResearchThrough')
   if item.get('symbol') not in {'600900.SH','515450.SH'} or item.get('comparisonIsConsecutive') is not True or item.get('corporateActionReviewRequired') or not before or not through or not before<through:continue
   direction='worsened' if change=='趋势恶化（完整日由MA20上方转下方）' else 'improved' if change=='趋势改善（完整日由MA20下方转上方）' else None
   if direction:value['datedHoldingComparisons'].append({'symbol':item['symbol'],'fromDate':before,'through':through,'direction':direction})
 value['holdingChanges']=[]
 if allowed:
  before={p['symbol']:p for p in summary(previous)['positions']}
  for item in positions:
   old=before.get(item['symbol'],{})
   if item['symbol'] in names() and old.get('trend') in {'above','below','equal'} and item['trend'] in {'above','below','equal'} and old['trend']!=item['trend']:
    value['holdingChanges'].append({'symbol':item['symbol'],'from':old['trend'],'to':item['trend']})
 from .research_state import build as research_states
 study=research_states(report,previous)
 selected={i['symbol']:i for i in study['items']}
 value['researchUpdates']=[]
 for symbol in study['attentionSymbols']:
  item=selected[symbol];change=item['change']
  value['researchUpdates'].append({'symbol':symbol,'state':item['state'],'through':item['through'],
   'quoteSourceTime':item['quoteSourceTime'],'comparable':change['comparable'],
   'newlyMet':change['newlyMet'],'invalidated':change['invalidated'],
   'stateChanged':change['stateChanged'],'previousStateLabel':change.get('previousLabel'),
   'proximityChange':change.get('proximityChange'),'proximity':item['proximity']['status'],
   'missingRules':[r['id'] for r in item['rules'] if r['status']=='failed'][:2],
   'dataInsufficient':not item['available'] or not item['currentResearchAllowed'],
   'researchQualified':item['researchQualified']})
 value['holdingRiskItems']=[{'symbol':i['symbol'],'code':'corporate-action-review'} for i in report.get('instruments',[]) if i.get('symbol') in names() and (i.get('quote') or {}).get('corporateActionReviewRequired')]
 scan=report.get('marketDiscovery') or {};value['marketDiscoveries']=[]
 if scan.get('asOf')==report.get('generatedAt','')[:10] and scan.get('status') in {'complete-provider-snapshot','partial-provider-snapshot'} and not scan.get('synthetic'):
  for item in scan.get('items',[])[:3]:
   symbol=item.get('symbol');name=item.get('name');stamp=item.get('sourceTimestamp')
   if isinstance(symbol,str) and re.fullmatch(r'\d{6}\.(SH|SZ)',symbol) and isinstance(name,str) and re.fullmatch(r'[\w\u3400-\u9fff *().\-]{1,40}',name) and stamp and item.get('tradeAdmitted') is False:
    value['marketDiscoveries'].append({'symbol':symbol,'name':name,'sourceTime':stamp,'state':'discovery-only'})
 return value

def _validate_notification(value):
 """Only catalog names, ISO source times and fixed phrases reach Bark."""
 if not isinstance(value,dict):return ''
 known=names();times=value.get('quoteTimes',[]);symbols=value.get('watchSymbols',[])
 if not isinstance(times,list) or len(times)>10 or not isinstance(symbols,list) or len(symbols)>4:raise ValueError('SUMMARY_INVALID')
 for stamp in times:
  parsed=datetime.fromisoformat(stamp)
  if parsed.tzinfo is None:raise ValueError('SUMMARY_TIME_INVALID')
 codes=value.get('riskCodes',[]);new=value.get('newRiskCodes',[])
 if any(s not in known for s in symbols) or any(c not in RISK_LABELS for c in [*codes,*new]):raise ValueError('SUMMARY_INVALID')
 if value.get('tradeCandidateCount')!=0:raise ValueError('UNVERIFIED_TRADE_CANDIDATE')
 changes=value.get('holdingChanges',[])
 if not isinstance(changes,list) or len(changes)>10:raise ValueError('SUMMARY_INVALID')
 if changes and value.get('comparisonAvailable') is not True:raise ValueError('SUMMARY_COMPARISON_UNVERIFIED')
 for item in changes:
  if item.get('symbol') not in known or item.get('from') not in {'above','below','equal'} or item.get('to') not in {'above','below','equal'}:raise ValueError('SUMMARY_INVALID')
 trends=value.get('holdingTrends',[])
 if not isinstance(trends,list) or len(trends)>10:raise ValueError('SUMMARY_INVALID')
 for item in trends:
  symbol=item.get('symbol');trend=item.get('trend');through=item.get('trendThrough')
  if symbol not in known or trend not in (None,'above','below','equal'):raise ValueError('SUMMARY_INVALID')
  if through is not None:
   if not isinstance(through,str) or len(through)!=10:raise ValueError('SUMMARY_INVALID')
   through=__import__('datetime').date.fromisoformat(through).isoformat()
 updates=value.get('researchUpdates',[])
 if not isinstance(updates,list) or len(updates)>3:raise ValueError('RESEARCH_SUMMARY_INVALID')
 from .research_state import RULES,LABELS
 rules={r[0]:r for r in RULES};seen=set()
 for update in updates:
  symbol=update.get('symbol');state=update.get('state');through=update.get('through');stamp=update.get('quoteSourceTime')
  if symbol not in known or symbol in seen or state not in {'insufficient','watch','near'}:raise ValueError('RESEARCH_SUMMARY_INVALID')
  seen.add(symbol)
  if through is not None:through=__import__('datetime').date.fromisoformat(through).isoformat()
  if stamp is not None:
   parsed=datetime.fromisoformat(stamp)
   if parsed.tzinfo is None:raise ValueError('RESEARCH_TIME_INVALID')
   stamp=parsed.isoformat()
  for key in ('comparable','stateChanged','dataInsufficient','researchQualified'):
   if type(update.get(key)) is not bool:raise ValueError('RESEARCH_SUMMARY_INVALID')
  for key in ('newlyMet','invalidated','missingRules'):
   members=update.get(key)
   if not isinstance(members,list) or len(members)>len(rules) or any(r not in rules for r in members):raise ValueError('RESEARCH_SUMMARY_INVALID')
  proximity=update.get('proximity');pchange=update.get('proximityChange');proximities={'unknown','near','triggered','not-near'}
  if proximity not in proximities or pchange is not None and (not isinstance(pchange,dict) or pchange.get('from') not in proximities or pchange.get('to') not in proximities):raise ValueError('RESEARCH_SUMMARY_INVALID')
  prior=update.get('previousStateLabel')
  if prior is not None and prior not in LABELS.values():raise ValueError('RESEARCH_SUMMARY_INVALID')
  if not update['comparable'] and (update['newlyMet'] or update['invalidated'] or update['stateChanged'] or pchange):raise ValueError('RESEARCH_COMPARISON_UNVERIFIED')
 held=value.get('holdingRiskItems',[])
 if not isinstance(held,list) or len(held)>10 or any(i.get('symbol') not in known or i.get('code') not in RISK_LABELS for i in held):raise ValueError('HOLDING_RISK_INVALID')
 dated=value.get('datedHoldingComparisons',[])
 if not isinstance(dated,list) or len(dated)>2:raise ValueError('DATED_HOLDING_COMPARISON_INVALID')
 for i in dated:
  if i.get('symbol') not in {'600900.SH','515450.SH'} or i.get('direction') not in {'worsened','improved'}:raise ValueError('DATED_HOLDING_COMPARISON_INVALID')
  before=__import__('datetime').date.fromisoformat(i['fromDate']);after=__import__('datetime').date.fromisoformat(i['through'])
  if before>=after:raise ValueError('DATED_HOLDING_COMPARISON_INVALID')
 discoveries=value.get('marketDiscoveries',[])
 if not isinstance(discoveries,list) or len(discoveries)>3:raise ValueError('MARKET_DISCOVERY_INVALID')
 for i in discoveries:
  if not isinstance(i.get('symbol'),str) or not re.fullmatch(r'\d{6}\.(SH|SZ)',i['symbol']) or not isinstance(i.get('name'),str) or not re.fullmatch(r'[\w\u3400-\u9fff *().\-]{1,40}',i['name']) or i.get('state')!='discovery-only':raise ValueError('MARKET_DISCOVERY_INVALID')
  if datetime.fromisoformat(i['sourceTime']).tzinfo is None:raise ValueError('MARKET_DISCOVERY_TIME_INVALID')


# Presentation only: these phrases describe the original rules, never alter them.
MOBILE_CONDITIONS = {
 'score':'研究分尚不足原门槛', 'trend':'等待收盘高于MA20且MA20高于MA60',
 'momentum':'等待动量与相对强弱满足', 'risk':'波动或回撤仍超原门槛',
 'liquidity':'历史流动性未过门槛', 'basis':'先核对原始价与复权趋势冲突',
 'correlation':'持仓相关性证据不足', 'new-position':'已有持仓，不算新增机会',
 'overlap':'先核对重复暴露', 'peer-coverage':'同类有效样本不足',
}


def notification_text(value):
 """Compact phone brief from the same validated, fixed-vocabulary evidence.

 No free-form report text, balances, holdings sizes or access keys reach Bark.
 The full original technical analysis remains in the report, not this message.
 """
 _validate_notification(value)  # Preserve all input/trust checks.
 if not isinstance(value,dict):return ''
 from .research_state import RULES,LABELS
 known=names();rules={r[0]:r[1] for r in RULES}
 codes=value.get('riskCodes',[]);new=value.get('newRiskCodes',[])
 risk=bool(new) or 'market-unverified' in codes or 'below-ma20' in codes or 'adjustment-conflict' in codes
 lines=['今日结论：'+('风险提醒' if risk else '继续观察')+'；完整准入交易候选：0（暂无通过核验的交易候选）。']
 changes=value.get('holdingChanges',[])
 if changes:
  labels={'above':'高于MA20','below':'低于MA20','equal':'等于MA20'}
  holding='；'.join(known[c['symbol']]+' '+c['symbol']+'：完整日由'+labels[c['from']]+'转为'+labels[c['to']] for c in changes)
 elif value.get('datedHoldingComparisons'):
  holding='历史确认持仓的完整日研究：'+'；'.join(known[i['symbol']]+' '+i['symbol']+' '+i['through']+('跌到MA20下方，重核风险' if i['direction']=='worsened' else '回到MA20上方，仍需准入核验') for i in value['datedHoldingComparisons'])+'；不是当前账户或盘中触发'
 elif new:holding='新增'+ '、'.join(RISK_LABELS[c] for c in new)
 elif value.get('comparisonAvailable'):holding='无新增已识别风险'
 else:holding='缺少可比报告，重要变化未核验'
 if value.get('holdingRiskItems'):holding+='；'+'；'.join(known[i['symbol']]+' '+i['symbol']+'：'+RISK_LABELS[i['code']] for i in value['holdingRiskItems'])
 lines.append('我的持仓：'+holding+'。')
 discoveries=value.get('marketDiscoveries',[])
 updates=value.get('researchUpdates',[])[:2 if discoveries else 3]
 if updates:
  lines.append('新机会（研究观察，非交易信号）：')
  for item in updates:
   change=[]
   if item['comparable']:
    if item['stateChanged']:change.append((item.get('previousStateLabel') or '此前状态')+'→'+LABELS[item['state']])
    if item['newlyMet']:change.append('新满足'+'、'.join(rules[r] for r in item['newlyMet']))
    if item['invalidated']:change.append('失效'+'、'.join(rules[r] for r in item['invalidated']))
    if item.get('proximityChange'):change.append('突破位置档位变化，仍需完整核验')
   if item['dataInsufficient']:condition='数据不足，暂停方向判断'
   elif item['missingRules']:condition=MOBILE_CONDITIONS[item['missingRules'][0]]
   elif item['proximity']=='near':condition='关注下一完整日突破前20日高点；尚未确认突破'
   elif item['proximity']=='triggered':condition='原完整日已突破高点，仍缺完整交易准入'
   else:condition='核对下一完整日趋势和相对强弱，仍缺完整交易准入'
   lines.append(known[item['symbol']]+' '+item['symbol']+'｜'+LABELS[item['state']]+'；'+('；'.join(change)+'；' if change else '')+condition+'。')
  if any(not i['comparable'] for i in updates):lines.append('缺少可比研究基线，不声称新触发。')
 elif not discoveries:
  symbols=value.get('watchSymbols',[])[:3]
  lines.append('新机会：'+('、'.join(known[s]+' '+s for s in symbols)+'；条件证据不足，仅观察。' if symbols else '无可用观察项，不沿用旧名单。'))
 for item in discoveries[:max(0,3-len(updates))]:
  lines.append('全市场发现：'+item['name']+' '+item['symbol']+'｜延迟快照线索；先核验完整日MA20/60、相对强弱与原门槛，尚非交易候选。')
 times=sorted({datetime.fromisoformat(s).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S+08:00') for s in value.get('quoteTimes',[])})
 if times:
  date=times[0][:10];stamp=times[0] if len(times)==1 else times[0]+'～'+(times[-1][11:] if times[-1][:10]==date else times[-1])
 else:stamp='未取得可靠时间'
 dates=sorted({i['through'] for i in updates if i.get('through')} | {i['trendThrough'] for i in value.get('holdingTrends',[]) if i.get('trendThrough')})
 lines.append('数据：报价'+stamp+'；完整日研究'+(' / '.join(dates) or '未核验')+'；'+('报价未通过盘中核验' if 'market-unverified' in codes else '报价通过本次核验，非持续实时')+'。')
 warnings=[]
 if 'account-dated' in codes:warnings.append('持仓/现金为有日期快照，待对账')
 if 'adjustment-conflict' in codes:warnings.append('价格口径冲突')
 if 'below-ma20' in codes:warnings.append('持仓完整日低于MA20')
 if 'corporate-action-review' in codes:warnings.append('除权除息待核验')
 warnings.append('资金/估值证据未齐')
 lines.append('有效性：'+'；'.join(warnings)+'；跌回MA20、相对强弱转负或数据失效，撤销观察。')
 return '\n'.join(lines)
