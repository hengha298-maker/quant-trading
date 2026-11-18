"""Read-only explanation of existing holding scenarios, not a new strategy.

Reuses scenario gates, complete-session trend plugins and archived backtests.
It never fetches prices, recalculates holdings/PnL, changes rule thresholds,
chooses a winning backtest, or turns missing evidence into an order condition.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import re

BJ = timezone(timedelta(hours=8))


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo is not None else None
    except (AttributeError, ValueError, TypeError):
        return None


def _texts(values):
    return list(dict.fromkeys(x for x in values if isinstance(x, str) and x))


def _history(instrument, radar):
    """Keep unlike legacy cash-flow and fixed-rule studies separate."""
    legacy = instrument.get('backtest') or {}
    legacy_rows = legacy.get('comparison') or []
    baseline = next((r for r in legacy_rows if r.get('strategy') == 'buy_hold'), None)
    comparisons = []
    for row in legacy_rows:
        if row.get('strategy') == 'buy_hold':
            continue
        a, b = row.get('totalReturnBps'), (baseline or {}).get('totalReturnBps')
        comparisons.append({'strategy': row.get('strategy'), 'returnBps': a,
                            'buyHoldReturnBps': b,
                            'excessVsBuyHoldBps': a-b if type(a) is int and type(b) is int else None,
                            'sessions': row.get('tradingDays'), 'trades': row.get('tradeCount'),
                            'costCNYCents': row.get('cost')})
    item = next((r for r in (radar or {}).get('items', []) if r.get('symbol') == instrument.get('symbol')), {})
    lab = item.get('lab') or {}
    quarantine_study = (item.get('trustResearch') or {}).get('current') or {}
    strategies = lab.get('strategies') or {}
    fixed = []
    for name, value in strategies.items():
        if name == 'buy_hold' or value.get('applicable') is False:
            continue
        folds = {}
        for fold in ('train', 'validation', 'test'):
            left = (value.get('partitions') or {}).get(fold) or {}
            right = ((strategies.get('buy_hold') or {}).get('partitions') or {}).get(fold) or {}
            lm, rm = left.get('metrics') or {}, right.get('metrics') or {}
            a, b = lm.get('totalReturnBps'), rm.get('totalReturnBps')
            aligned = bool(left.get('from') and left.get('to') and left.get('from') == right.get('from')
                           and left.get('to') == right.get('to') and lm.get('tradingDays') == rm.get('tradingDays'))
            folds[fold] = {'from': left.get('from'), 'to': left.get('to'),
                           'sessions': lm.get('tradingDays'), 'returnBps': a,
                           'buyHoldReturnBps': b,
                           'sameInterval': aligned,
                           'excessVsBuyHoldBps': a-b if aligned and type(a) is int and type(b) is int else None,
                           'trades': lm.get('tradeCount'), 'costCNYCents': lm.get('cost')}
        fixed.append({'strategy': name, 'partitions': folds})
    return {
        'status': 'research-only-not-proven',
        'conclusion': '已有历史价格研究不能证明该持仓情景具有稳定盈利能力。',
        'legacyPriceStudy': {'available': bool(legacy_rows), 'from': legacy.get('from'),
                             'to': legacy.get('to'), 'basis': legacy.get('basis'),
                             'costAssumptions': deepcopy(legacy.get('config')),
                             'comparisons': comparisons,
                             'note': '原V1前收盘/MA20及交叉规则的价格路径研究；不当作当前MA20/60持仓情景的独立验证。'},
        'fixedRuleStudy': {'available': bool(strategies), 'from': lab.get('from'), 'to': lab.get('to'),
                          'inputHash': lab.get('inputHash'), 'modelRevision': lab.get('modelRevision'),
                          'costAssumptions': deepcopy(lab.get('config')), 'strategies': fixed,
                          'signalBasis': lab.get('signalBasis'),
                          'prospectiveValidated': False, 'completeTotalReturn': lab.get('completeTotalReturn') is True,
                          'statisticalAssessmentAvailable': bool(item.get('strategyQuality')),
                          'statisticalConclusion': [r.get('conclusion') for r in item.get('strategyQuality', []) if r.get('conclusion')],
                          'note': '同名固定规则仍不是含用户预算、分红现金流和实时账户约束的整套持仓建议验证。'},
        'quarantinedArchive': {'available': bool(quarantine_study), 'from': quarantine_study.get('from'),
                               'to': quarantine_study.get('to'), 'inputHash': quarantine_study.get('inputHash'),
                               'sourceStatus': (item.get('quality') or {}).get('status'),
                               'corporateActionCoverageComplete': (quarantine_study.get('coverage') or {}).get('complete') is True,
                               'currentSignalAllowed': False,
                               'note': '隔离标的已有明确日期的原始价历史研究；仅保留来源，不将其补作当前行情、准入或完整含息结果。'},
        'limitations': _texts([
            '前一完整交易日信号、下一可用交易日开盘假设，代码窗口未读取执行日价格作为信号；供应商历史复权版本仍未获点时认证。',
            '佣金、最低佣金、滑点和股票卖出印花税是显式研究假设，不是用户实际费率；买入持有基准使用同一研究口径。',
            '完整分红、税款、拆分及再投资现金流未全部核验，价格路径收益不等于持仓真实总回报。',
            '停牌、零量及涨跌停仅有限度保守过滤；开盘排队和成交冲击无法用日线还原。',
            '固定观察池存在选择和幸存者偏差；历史留出不是前瞻验收，不挑收益最佳策略形成建议。',
            '当前情景尚无独立前瞻样本；样本量和交易次数必须随各研究区间分别查看。',
            *legacy.get('limitations', []), *lab.get('limitations', []),
        ]),
    }


def build(instrument, portfolio, *, now=None, radar=None):
    """Explain one held symbol using supplied, already-validated evidence only.

    Call after scenarioAnalysis/research are built, and again after freshness
    downgrade. No raw daily dataset is needed; the original trend plugin retains
    its MA60/date and historical comparisons remain read-only.
    """
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError('HOLDING_DECISION_AWARE_CLOCK_REQUIRED')
    symbol = instrument.get('symbol')
    if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(?:SH|SZ)', symbol):
        raise ValueError('HOLDING_DECISION_SYMBOL_REQUIRED')
    quote = instrument.get('quote') or {}
    quality = quote.get('quality') or {}
    scenario = instrument.get('scenarioAnalysis') or {}
    evidence = scenario.get('evidenceQuality') or {}
    sf = scenario.get('facts') or {}
    metrics = instrument.get('metrics') or {}
    research = instrument.get('research') or {}
    trend = next((p for p in research.get('plugins', []) if p.get('id') == 'trend-20-60'), {})
    positions = [p for p in (portfolio or {}).get('positions', []) if p.get('symbol') == symbol]
    stamp, fetched = _stamp(quote.get('sourceTimestamp')), _stamp(quote.get('fetchedAt'))
    age = (clock-stamp).total_seconds() if stamp else None
    price = _number(quote.get('price'))
    display_ok = bool(quote.get('symbol') == symbol and price is not None and price > 0 and
                      quality.get('unitVerified') is True and quote.get('adjustment') == 'none' and
                      stamp and stamp <= clock and (quality.get('displayUsable') is True or
                      quality.get('status') in {'intraday-verified', 'closing-snapshot', 'historical-snapshot'}))
    live = bool(display_ok and evidence.get('currentIntradayVerified') is True and
                quality.get('status') == 'intraday-verified' and quality.get('intradaySignalAllowed') is True and
                stamp.astimezone(BJ).date() == clock.astimezone(BJ).date() and 0 <= age <= 90)
    local = clock.astimezone(BJ)
    closing = bool(display_ok and evidence.get('closingSnapshot') is True and quality.get('status') == 'closing-snapshot'
                   and stamp.astimezone(BJ).date() == local.date() and local.hour >= 15
                   and stamp.astimezone(BJ).hour >= 15)
    action_basis = bool((live or closing) and
                        not (scenario.get('modelConditions') or {}).get('adjustmentConflict') and
                        not quote.get('corporateActionReviewRequired') and
                        'CORPORATE_ACTION_PRICE_BASIS_UNVERIFIED' not in quality.get('codes', []))
    through = sf.get('trendThrough')
    # Model numbers can only be presented together when all share a complete
    # session. Do not silently substitute a newer/older MA60 from another date.
    matched = bool(through and trend.get('through') == through)
    ma20 = sf.get('ma20')
    ma60 = trend.get('ma60') if matched and _number(trend.get('ma60')) is not None else None
    volume_ratio = sf.get('completedDayVolumeRatio')
    risks = _texts([*evidence.get('reasons', []), *instrument.get('risks', []),
                    *(r for s in scenario.get('scenarios', []) for r in s.get('risks', [])),
                    *trend.get('risks', [])])
    gaps = []
    def missing(code, text):
        gaps.append({'code': code, 'reason': text})
    if not display_ok:
        missing('VERIFIED_QUOTE_MISSING', '缺少可展示的已核验报价；不估算当前价格、盈亏或买卖价。')
    if not live:
        missing('CURRENT_INTRADAY_QUOTE_UNVERIFIED', '当前盘中报价未通过；历史价格和完整日研究不能用作现在的交易依据。')
    if quote.get('corporateActionReviewRequired') or 'CORPORATE_ACTION_PRICE_BASIS_UNVERIFIED' in quality.get('codes', []):
        missing('CORPORATE_ACTION_RECONCILIATION_REQUIRED', '除权/除息或证券状态变化尚未核验；源昨收可能是除权参考价，不与旧日线或成本直接混作买卖阈值。')
    if not positions or not all(p.get('currentHoldingConfirmed') is True for p in positions):
        missing('ACCOUNT_SNAPSHOT_DATED', '数量和显示成本来自明确日期快照，期间成交及当前可卖数量尚未对账。')
    if not through or ma20 is None:
        missing('COMPLETE_SESSION_MA20_MISSING', '缺少可核验的完整交易日MA20。')
    if ma60 is None:
        missing('MATCHING_SESSION_MA60_MISSING', '没有与MA20同日期的已验证MA60，不猜趋势。')
    if volume_ratio is None:
        missing('COMPLETE_SESSION_VOLUME_MISSING', '完整日成交量对前19日均量尚不足，不用盘中累计量替代全天量。')
    if not (scenario.get('modelConditions') or {}).get('adjustedSameDateComparison'):
        missing('ADJUSTED_SAME_DAY_CONTEXT_MISSING', '缺少同日期复权对照，先核验公司行为。')
    missing('CASH_RISK_BUDGET_UNVERIFIED', '快照现金、目标仓位、实际费用及个人最大损失预算未完整核验，不计算可买数量。')
    missing('VALUATION_FUND_FLOW_INCOMPLETE', '基本估值、资金流及完整分红证据不足，不能据趋势推断价值低估。')

    conditions = []
    for item in scenario.get('scenarios', []):
        status = item.get('status', 'blocked')
        if item.get('id') != 'watch' and not action_basis:
            status = 'blocked'
        conditions.append({'id': item.get('id'), 'label': item.get('label'), 'status': status,
                           'trigger': item.get('trigger'),
                           'meaningIfMet': '仅说明该项既有研究条件可继续核查；完整准入、账户与预算约束仍须同时满足。',
                           'invalidation': trend.get('invalidation') or '行情失效、复权冲突或账户证据改变时，暂停原研究判断。',
                           'basis': 'existing-holding-scenario-no-threshold-change',
                           'risks': deepcopy(item.get('risks', [])), 'orderAllowed': False})
    conditions.append({'id': 'exit', 'label': '退出与判断失效研究', 'status': 'reference-only' if action_basis else 'blocked',
                       'trigger': trend.get('invalidation') or '先补齐同口径完整日趋势、公司行为与有效报价，再研究退出依据。',
                       'meaningIfMet': '原持有研究依据失效，应重新评估风险；不能直接推出必须清仓或具体止损价。',
                       'invalidation': '除权价格参考变化、数据过期或账户未对账时，不以历史均线触发实时退出。',
                       'basis': 'existing-trend-plugin-invalidation', 'risks': deepcopy(risks), 'orderAllowed': False})
    model = []
    if ma20 is not None and ma60 is not None and matched:
        close = sf.get('completedClose')
        model.append({'kind': 'model-inference', 'through': through,
                      'basis': 'complete-unadjusted-daily-price-only',
                      'summary': trend.get('summary'), 'close': close, 'ma20': ma20, 'ma60': ma60,
                      'adjustedDirection': trend.get('adjustedDirection'),
                      'adjustmentConflict': trend.get('adjustmentConflict') is True,
                      'notCurrentTradeSignal': True})
    conclusion = scenario.get('summary') or '数据证据不足，保留观察。'
    if not action_basis:
        conclusion = ('当前只作观察：除权/除息价格口径尚未核验，先对齐公司行为；不能沿用旧价格阈值。'
                      if quote.get('corporateActionReviewRequired') or 'CORPORATE_ACTION_PRICE_BASIS_UNVERIFIED' in quality.get('codes', [])
                      else '当前只作观察：缺少本次有效报价或同口径依据；完整日历史事实保留，暂不形成当前加减仓判断。')
    return {
        'format': 'goose-holding-decision-evidence', 'version': 1, 'symbol': symbol,
        'name': instrument.get('name') or symbol, 'generatedAt': clock.isoformat(),
        'conclusion': conclusion,
        'researchOnly': True, 'currentActionAllowed': False,
        'facts': {
            'holding': {'status': (portfolio or {}).get('status'), 'positions': [
                {k: p.get(k) for k in ['symbol','name','quantity','costPrice','broker','holdingAsOf',
                                       'currentHoldingConfirmed','reportedSourceKind','costBasis','feeBasis']}
                for p in positions], 'source': 'dated-user-confirmed-snapshot-and-explicit-ledger-events-no-broker-api',
                'costReconciliationRequired': True},
            'quote': {'source': quote.get('source'), 'price': str(price) if display_ok else None,
                      'sourceTime': quote.get('sourceTimestamp'), 'fetchedAt': quote.get('fetchedAt'),
                      'ageSeconds': round(age, 3) if age is not None else None,
                      'fetchAgeSeconds': round((clock-fetched).total_seconds(), 3) if fetched else None,
                      'status': quality.get('status', 'missing'), 'intradayVerified': live,
                      'displayUsable': display_ok, 'basis': 'unadjusted-CNY-per-share',
                      'timestampKind': quote.get('timestampKind'),
                      'corporateActionReviewRequired': quote.get('corporateActionReviewRequired') is True,
                      'failureCodes': deepcopy(quality.get('codes', []))},
            'technical': {'through': through, 'completedClose': sf.get('completedClose'),
                          'ma20': ma20, 'ma60': ma60, 'volumeRatioVsPrior19CompleteDays': volume_ratio,
                          'volumeNote': '只比较完整日成交量；盘中累计量不与全天量直接比较。',
                          'basis': 'complete-unadjusted-daily-price-only'},
            'valuation': {'positions': [{k: p.get(k) for k in ['symbol','marketValue','computedPnl','valuationAt','valuationStatus','costBasis','feeBasis']} for p in positions],
                          'basis': 'reuse-existing-accounting-values-no-recalculation',
                          'note': '仅按有日期报价和显示成本重估的差额；未扣完整未来费用，不等于可兑现净收益。'},
        },
        'interpretation': model, 'conditions': conditions,
        'referenceLevels': {'through': through, 'basis': 'complete-session-unadjusted-indicators-not-order-prices', 'orderPricesVerified': False,
                            'ma20': ma20, 'ma60': ma60, 'entryRange': None, 'stopLossPrice': None,
                            'targetRange': None, 'riskRewardRatio': None,
                            'reason': '原有持仓规则未定义已核验的入场区间、目标价及止损价；均线仅为可复核研究条件，不补造数值。'},
        'dataGaps': gaps, 'risks': risks,
        'historyEvidence': _history(instrument, radar),
    }
