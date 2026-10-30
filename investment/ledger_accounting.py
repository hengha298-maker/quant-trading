"""Decimal, evidence-gated account reconciliation. No broker state is mutated.

All intervals are (opening EOD, closing EOD]. Cash is posted on its explicit
credit/debit date, not on an inferred settlement date. Return interfaces keep
TWR/MWR separate; Modified Dietz is labelled an estimate, never an exact TWR.
"""
from datetime import date,timedelta
from decimal import Decimal as D,localcontext
from .ledger import money

def issue(code,message,**details):return {'code':code,'message':message,**details}

def coverage(batches,broker,start,end,role):
    cursor=start
    intervals=[]
    for b in batches:
        if b['broker']!=broker or not b['completeCoverageDeclared']:continue
        r=b.get('sourceRole','combined')
        if r not in {'combined',role}:continue
        intervals.append((b['coverageStart'],b['coverageEnd']))
    for left,right in sorted(intervals):
        if left<=(date.fromisoformat(cursor)+timedelta(days=1)).isoformat() and right>cursor:cursor=right
    return {'completeDeclared':cursor>=end,'coveredThrough':min(cursor,end),
            'missingPeriod':None if cursor>=end else {'from':max(start,(date.fromisoformat(cursor)+timedelta(days=1)).isoformat()),'to':end}}

def cash_postings(events,issues):
    """Explicit linked cash lines supersede a trade's derived cash exactly once."""
    by_id={};ambiguous=set()
    for e in events:
        if e['id'] in by_id:ambiguous.add(e['id'])
        by_id[e['id']]=e
    for e in events:
        if e.get('linkId') in ambiguous or e.get('includedInEventId') in ambiguous:
            issues.append(issue('REFERENCE_DATE_AMBIGUOUS','关联编号跨日期重复，无法确定所指成交/费用。',eventId=e['id']))
    settled={};posted=[]
    for e in events:
        if e['kind']=='SETTLEMENT':
            ref=e.get('linkId')
            if e.get('sourceRole')=='executions':
                issues.append(issue('EXECUTION_SOURCE_SETTLEMENT_INVALID','仅成交来源不能作为已到账交收证据。',eventId=e['id']));continue
            if not ref or ref not in by_id or ref in ambiguous or by_id[ref]['kind'] not in {'BUY','SELL'}:
                issues.append(issue('UNMATCHED_SETTLEMENT','资金交收记录缺少对应逐笔成交。',eventId=e['id']));continue
            if ref in settled:
                issues.append(issue('DUPLICATE_SETTLEMENT','同一成交出现多笔资金交收，需核对是否分笔或重复。',eventId=e['id']));continue
            settled[ref]=e
    for e in events:
        kind=e['kind']
        if kind=='SETTLEMENT' and settled.get(e.get('linkId')) is not e:continue
        if e.get('sourceRole')=='executions':continue
        if kind in {'DIVIDEND_RECEIVABLE','POSITION_CHANGE'}:continue
        if e.get('includedInEventId'):
            parent=by_id.get(e['includedInEventId'])
            if kind not in {'FEE','TAX'} or not parent or parent.get('fees') is None or D(e['gross'])>D(parent['fees']):
                issues.append(issue('FEE_REFERENCE_INVALID','费用已含关系无法核实。',eventId=e['id']))
            continue
        if kind in {'BUY','SELL'} and (e.get('sourceRole')=='executions' or e['id'] in settled):continue
        cash=e.get('netCash');basis='reported-net'
        if cash is None:
            basis='gross-less-explicit-fees'
            if kind=='CASH_IN':cash=e['gross']
            elif kind in {'CASH_OUT','TAX','FEE'}:cash=str(-D(e['gross']))
            elif kind in {'BUY','SELL','DIVIDEND'} and e.get('fees') is not None:
                cash=str(-D(e['gross'])-D(e['fees'])) if kind=='BUY' else str(D(e['gross'])-D(e['fees']))
        if cash is None:
            issues.append(issue('CASH_AMOUNT_UNKNOWN','缺少净发生额或完整费用，现金变动未知。',eventId=e['id']));continue
        if not e.get('cashDate'):
            issues.append(issue('CASH_DATE_UNKNOWN','未取得实际资金发生/到账日，不假定成交日已经到账。',eventId=e['id']));continue
        if kind=='SETTLEMENT':
            parent=by_id[e['linkId']]
            expected=parent.get('netCash')
            if expected is None and parent.get('fees') is not None:
                expected=str(-D(parent['gross'])-D(parent['fees'])) if parent['kind']=='BUY' else str(D(parent['gross'])-D(parent['fees']))
            if expected is not None and abs(D(expected)-D(cash))>D('.01'):
                issues.append(issue('SETTLEMENT_AMOUNT_DIFF','成交推算净额与实际交收金额不同。',eventId=e['id']))
            if parent['kind']=='BUY' and D(cash)>=0 or parent['kind']=='SELL' and D(cash)<=0:
                issues.append(issue('SETTLEMENT_DIRECTION_DIFF','交收方向与成交不符。',eventId=e['id']))
        posted.append({'event':e,'date':e['cashDate'],'cash':D(cash),'basis':basis})
    # Embedded costs plus another cash debit with the same explicit economic
    # reference require an explanation rather than automatic double deduction.
    for p in posted:
        e=p['event']
        if e['kind'] in {'TAX','FEE'} and e.get('linkId'):
            candidates=[x for x in events if x['kind'] in {'BUY','SELL','DIVIDEND'} and (x['id']==e['linkId'] or x.get('linkId')==e['linkId'])]
            if any(x.get('fees') is not None and D(x['fees'])>0 for x in candidates):
                issues.append(issue('POSSIBLE_DOUBLE_FEE','关联成交/红利已含费用，又出现独立扣费；请确认是否同一笔。',eventId=e['id']))
    included={}
    for e in events:
        ref=e.get('includedInEventId')
        if ref:included[ref]=included.get(ref,D(0))+D(e['gross'])
    for ref,total in included.items():
        if ref in by_id and by_id[ref].get('fees') is not None and total>D(by_id[ref]['fees'])+D('.01'):
            issues.append(issue('INCLUDED_FEES_EXCEED_TOTAL','已含费用分项大于总费用。',eventId=ref))
    return posted

def nav(statement):
    if statement.get('cashBalance') is None or any(p.get('marketValue') is None for p in statement['positions']):return None
    return D(statement['cashBalance'])+sum((D(p['marketValue']) for p in statement['positions']),D(0))

def reconcile_period(opening,closing,batches):
    if opening['broker']!=closing['broker'] or opening['asOf']>=closing['asOf']:raise ValueError('STATEMENT_PERIOD_INVALID')
    with localcontext() as context:
        context.prec=50
        return _period(opening,closing,batches)

def _period(opening,closing,batches):
    broker=opening['broker'];start,end=opening['asOf'],closing['asOf'];issues=[]
    if not opening.get('completeAccountDeclared') or not closing.get('completeAccountDeclared'):
        issues.append(issue('ACCOUNT_SCOPE_UNCONFIRMED','尚未确认两端快照包含普通现金账户全部持仓和总现金，不能宣称账户总收益。'))
    cv={role:coverage(batches,broker,start,end,role) for role in ('executions','cash')}
    for role,c in cv.items():
        if not c['completeDeclared']:issues.append(issue('COVERAGE_GAP','未声明完整覆盖的期间必须核对。',domain=role,period=c['missingPeriod']))
    all_events=[{**e,'sourceRole':e.get('sourceRole',b.get('sourceRole','combined'))} for b in batches if b['broker']==broker for e in b['events']]
    events=[e for e in all_events if start<e['date']<=end or e.get('cashDate') and start<e['cashDate']<=end]
    references={e.get('linkId') for e in events if e['kind']=='SETTLEMENT'}
    for e in all_events:
        if e['id'] in references and e not in events:events.append(e)
    # Date-only records preserve their uncertainty; they do not become actual
    # settlement facts just because they pass a schema check.
    for e in events:
        if e['kind'] in {'BUY','SELL'} and not e.get('executionDate'):
            issues.append(issue('EXECUTION_DATE_UNKNOWN','成交日期口径不明确。',eventId=e['id']))
        if e['kind']=='DIVIDEND' and e.get('fees') is None:
            issues.append(issue('DIVIDEND_TAX_EVIDENCE_UNKNOWN','红利净现金可保留，但所含税费口径未知，不能声明完整净分红收益。',eventId=e['id']))
        if e['kind']=='TAX' and not e.get('linkId') and not e.get('includedInEventId'):
            issues.append(issue('TAX_REFERENCE_UNKNOWN','独立税款缺少对应成交或分红关系，不自动分摊。',eventId=e['id']))
    rows={p['symbol']:{'quantity':p['quantity'],'cost':D(p['carryingCost']) if p.get('carryingCost') is not None and opening['costBasis']=='moving-average-acquisition-including-buy-fees' else None,'realized':D(0),'realizedKnown':True} for p in opening['positions']}
    realized=D(0);realized_known=True;fee_total=D(0);fees_known=True;gross_buy=D(0);gross_sell=D(0)
    trades=sorted((e for e in events if start<(e.get('executionDate') or e['date'])<=end and e['kind'] in {'BUY','SELL','POSITION_CHANGE'}),key=lambda e:(e.get('executionDate') or e['date'],e.get('executionTime') or '',e['id']))
    daily={}
    for e in trades:
        if e['kind'] in {'BUY','SELL'}:daily.setdefault((e.get('executionDate') or e['date'],e['symbol']),[]).append(e)
    ambiguous={key for key,group in daily.items() if len({e['kind'] for e in group})>1 and (any(not e.get('executionTime') for e in group) or len({e['executionTime'] for e in group})!=len(group))}
    for d,s in ambiguous:issues.append(issue('INTRADAY_ORDER_UNKNOWN','同日同标的既买又卖但缺少可靠成交时间，数量可核对，移动成本及已实现盈亏暂停。',date=d,symbol=s))
    for e in trades:
        s=e['symbol'];r=rows.setdefault(s,{'quantity':0,'cost':D(0),'realized':D(0),'realizedKnown':True})
        if ((e.get('executionDate') or e['date']),s) in ambiguous:r['cost']=None;r['realizedKnown']=False
        if e['kind']=='POSITION_CHANGE':
            r['quantity']+=e['positionDelta']
            r['cost']=None if r['cost'] is None or e.get('costDelta') is None else r['cost']+D(e['costDelta'])
            issues.append(issue('CORPORATE_ACTION_REVIEW','股份/成本变动已纳入数量，但总收益仍须核验企业行为及外部转入估值。',eventId=e['id']))
            continue
        q=e['quantity'];gross=D(e['gross']);fees=D(e['fees']) if e.get('fees') is not None else None
        if fees is None:fees_known=False;issues.append(issue('FEE_UNKNOWN','实际成交费用不完整。',eventId=e['id']))
        else:fee_total+=fees
        if e['kind']=='BUY':
            r['quantity']+=q;gross_buy+=gross
            r['cost']=None if r['cost'] is None or fees is None else r['cost']+gross+fees
        else:
            gross_sell+=gross
            if q>r['quantity']:issues.append(issue('NEGATIVE_POSITION','卖出数量超过已知持仓，可能缺历史记录。',eventId=e['id']));r['cost']=None
            removed=r['cost']*q/r['quantity'] if r['cost'] is not None and r['quantity']>0 else None
            if removed is None or fees is None:r['realizedKnown']=False;realized_known=False
            else:r['realized']+=gross-fees-removed;realized+=gross-fees-removed;r['cost']-=removed
            r['quantity']-=q
    postings=cash_postings(events,issues);within=[p for p in postings if start<p['date']<=end]
    cash_change=sum((p['cash'] for p in within),D(0));external=sum((p['cash'] for p in within if p['event']['kind'] in {'CASH_IN','CASH_OUT'}),D(0))
    expected_cash=D(opening['cashBalance'])+cash_change if opening.get('cashBalance') is not None else None
    cash_difference=D(closing['cashBalance'])-expected_cash if expected_cash is not None and closing.get('cashBalance') is not None else None
    if cash_difference is None:issues.append(issue('CASH_BALANCE_UNKNOWN','缺少期初或期末账面总现金，可用资金不能替代。'))
    elif abs(cash_difference)>D('.01'):issues.append(issue('CASH_DIFFERENCE','期末现金与期初加资金变动不符。',difference=money(cash_difference)))
    endmap={p['symbol']:p for p in closing['positions']};position_rows=[];unrealized=D(0);unrealized_known=True
    initial_unrealized=D(0);initial_known=True
    for p in opening['positions']:
        if opening['costBasis']!='moving-average-acquisition-including-buy-fees' or p.get('marketValue') is None or p.get('carryingCost') is None:initial_known=False
        else:initial_unrealized+=D(p['marketValue'])-D(p['carryingCost'])
    for s in sorted(set(rows)|set(endmap)):
        r=rows.get(s,{'quantity':0,'cost':D(0),'realized':D(0),'realizedKnown':True});actual=endmap.get(s,{'quantity':0,'carryingCost':'0','marketValue':'0','availableQuantity':0})
        diff=actual['quantity']-r['quantity']
        if diff:issues.append(issue('QUANTITY_DIFFERENCE','期初加成交/确认变动与期末数量不符。',symbol=s,difference=diff))
        cost_diff=D(actual['carryingCost'])-r['cost'] if actual.get('carryingCost') is not None and r['cost'] is not None and closing['costBasis']=='moving-average-acquisition-including-buy-fees' else None
        if cost_diff is not None and abs(cost_diff)>D('.01'):issues.append(issue('COST_DIFFERENCE','券商同口径成本与重放成本不符。',symbol=s,difference=money(cost_diff)))
        u=D(actual['marketValue'])-r['cost'] if r['cost'] is not None and actual.get('marketValue') is not None and not diff else None
        if u is None:unrealized_known=False
        else:unrealized+=u
        reported=[e.get('reportedRealizedPnl') for e in trades if e['symbol']==s and e['kind']=='SELL']
        broker_realized=sum((D(x) for x in reported),D(0)) if reported and all(x is not None for x in reported) else None
        realized_diff=r['realized']-broker_realized if broker_realized is not None and r['realizedKnown'] else None
        if realized_diff is not None and abs(realized_diff)>D('.01'):issues.append(issue('REALIZED_PNL_DIFFERENCE','重放已实现盈亏与券商原报不符，可能为费用/成本口径差异。',symbol=s,difference=money(realized_diff)))
        position_rows.append({'symbol':s,'projectedQuantity':r['quantity'],'brokerQuantity':actual['quantity'],'quantityDifference':diff,'availableQuantity':actual.get('availableQuantity'),
         'acquisitionCost':money(r['cost']),'brokerCarryingCost':actual.get('carryingCost'),'costDifference':money(cost_diff),'marketValue':actual.get('marketValue'),
         'unrealizedPnl':money(u),'brokerReportedUnrealizedPnl':actual.get('reportedPnl'),'brokerReportedRealizedPnl':money(broker_realized),'realizedPnlDifference':money(realized_diff),
         'realizedPnl':money(r['realized']) if r['realizedKnown'] else None})
    dividend_cash=sum((p['cash'] for p in within if p['event']['kind']=='DIVIDEND'),D(0))
    taxes=sum((-p['cash'] for p in within if p['event']['kind']=='TAX'),D(0));standalone_fees=sum((-p['cash'] for p in within if p['event']['kind']=='FEE'),D(0))
    dividends=[]
    for e in events:
        if e['kind']!='DIVIDEND_RECEIVABLE':continue
        ref=e.get('linkId') or e['id'];credits=[p for p in within if p['event']['kind']=='DIVIDEND' and p['event'].get('linkId')==ref]
        gross=sum((D(p['event']['gross']) for p in credits),D(0));net=sum((p['cash'] for p in credits),D(0));difference=D(e['gross'])-gross
        related_taxes=sum((-p['cash'] for p in within if p['event']['kind']=='TAX' and p['event'].get('linkId') in {ref,*[x['event']['id'] for x in credits]}),D(0))
        embedded=sum((D(x['event']['fees']) for x in credits),D(0)) if credits and all(x['event'].get('fees') is not None for x in credits) else None
        dividends.append({'reference':ref,'symbol':e['symbol'],'receivable':e['gross'],'creditedGross':money(gross),'creditedNet':money(net),'grossDifference':money(difference),
                          'includedFeesAndTax':money(embedded),'separateRelatedTax':money(related_taxes),'netAfterSeparateTax':money(net-related_taxes),
                          'taxBasis':'仅已导出实际费用/扣税，未来可能补扣的红利税未假定为零'})
        if difference:issues.append(issue('DIVIDEND_RECEIVABLE_DIFFERENCE','应收分红与已到账税前红利不一致，可能未到账或缺记录。',reference=ref,difference=money(difference)))
    for p in within:
        e=p['event']
        if e['kind']=='DIVIDEND' and not any((x.get('linkId') or x['id'])==e.get('linkId') for x in events if x['kind']=='DIVIDEND_RECEIVABLE'):
            issues.append(issue('DIVIDEND_ENTITLEMENT_MISSING','已记录实际红利现金，但缺少对应应收/权益证据，不能声明历史分红完整。',eventId=e['id']))
    first,last=nav(opening),nav(closing)
    if first is None or last is None:issues.append(issue('NAV_UNKNOWN','缺少完整期初/期末市值或账面现金。'))
    price_pnl=realized+unrealized-initial_unrealized if realized_known and unrealized_known and initial_known else None
    if price_pnl is not None and first is not None and last is not None:
        component_difference=(last-first-external)-(price_pnl+dividend_cash-taxes-standalone_fees)
        if abs(component_difference)>D('.01'):issues.append(issue('RETURN_COMPONENT_DIFFERENCE','净资产收益与价差/分红/税费拆分不一致，需核对遗漏。',difference=money(component_difference)))
    # All unresolved discrepancies block a verified total-return claim. Facts
    # and arithmetic differences remain visible for human reconciliation.
    reliable=not issues
    total=last-first-external if reliable and first is not None and last is not None else None
    simple=None;dietz=None
    external_rows=[p for p in within if p['event']['kind'] in {'CASH_IN','CASH_OUT'}]
    if total is not None and first>0:
        if not external_rows:simple=money(total/first*100)
        else:
            days=(date.fromisoformat(end)-date.fromisoformat(start)).days
            denominator=first+sum((p['cash']*D((date.fromisoformat(end)-date.fromisoformat(p['date'])).days)/D(days) for p in external_rows),D(0))
            if denominator>0:dietz=money(total/denominator*100)
    return {'broker':broker,'from':start,'to':end,'basis':'statement-EOD-moving-average-acquisition-cost-cash-basis-income',
      'status':'reconciled-declared-coverage' if reliable else 'needs-review','coverage':cv,'issues':issues,'positions':position_rows,
      'cash':{'opening':opening.get('cashBalance'),'postedNetChange':money(cash_change),'projectedClosing':money(expected_cash),'brokerClosing':closing.get('cashBalance'),'difference':money(cash_difference),
              'availableCash':closing.get('availableCash'),'frozenCash':closing.get('frozenCash'),'pendingDatedPostings':len([p for p in postings if p['date']>end])},
      'returns':{'openingNav':money(first),'closingNav':money(last),'externalNetInflow':money(external),'realizedPnl':money(realized) if realized_known else None,
                 'unrealizedPnl':money(unrealized) if unrealized_known else None,'pricePnlChange':money(price_pnl),
                 'creditedDividendNetBeforeSeparateTax':money(dividend_cash),'separateTax':money(taxes),'standaloneFees':money(standalone_fees),'tradeFees':money(fee_total) if fees_known else None,
                 'accountTotalPnl':money(total),'holdingPeriodReturnPct':simple,'modifiedDietzEstimatedPct':dietz,'timeWeightedReturn':None,'moneyWeightedReturn':None,
                 'rateBasis':'无外部流时为区间收益率；有外部流时仅提供按日末资金流假设的Modified Dietz估计，不冒充TWR/MWR。',
                 'totalReturnVerifiedForPeriod':reliable,'sinceInceptionVerified':False},'dividends':dividends,
      'limitations':['仅所填期间、所声明账户范围；数量/资金对平不是券商API核验。','区间前历史卖出/分红未完整提供，不宣称自开户完整收益。','可卖和冻结金额只使用期末券商原字段，不凭T+1推断。','同日买卖无可靠成交先后时不计算移动成本；空费用与未知成本不填零。']}

def overview(root):
    from .statements import load
    from .ledger import load_batches,BROKERS
    statements=load(root);batches=load_batches(root);accounts=[]
    for broker in sorted(BROKERS):
        points=sorted((s for s in statements if s['broker']==broker),key=lambda s:(s['asOf'],s['confirmedAt']))
        dates={p['asOf'] for p in points}
        if len(dates)<2:accounts.append({'broker':broker,'status':'statements-required','issues':[issue('STATEMENTS_REQUIRED','需要同口径期初及期末持仓/总现金快照，当前尚不能核实完整收益。')],'statementCount':len(points)});continue
        start=points[0]['asOf'];opening=next(s for s in reversed(points) if s['asOf']==start)
        accounts.append({**reconcile_period(opening,points[-1],batches),'statementCount':len(points)})
    good=all(a.get('returns',{}).get('totalReturnVerifiedForPeriod') for a in accounts)
    aligned=good and len({(a['from'],a['to']) for a in accounts})==1
    merged={'status':'reconciled-declared-coverage' if aligned else 'incomplete-or-different-periods','accountTotalPnl':money(sum((D(a['returns']['accountTotalPnl']) for a in accounts),D(0))) if aligned else None,
            'timeWeightedReturn':None,'moneyWeightedReturn':None,'warning':'合并收益要求两券商同区间且全部核对通过；跨券商转账在合并外部资金中应配对抵消，未自动当作利润。'}
    return {'version':1,'accounts':accounts,'combined':merged,'statementCount':len(statements),'automaticBrokerSync':False}
