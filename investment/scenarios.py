"""Conditional holding research; no orders, assumed cash, or invented share sizes.

All inputs are caller-supplied observations.  This module neither fetches prices
nor reads personal files.  Descriptive risk thresholds are explicit hypotheses,
not optimized trading parameters or a substitute for a user's risk budget.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

BJ = timezone(timedelta(hours=8))
NAMES = {"600900.SH": "长江电力", "515450.SH": "红利低波ETF"}
HUNDRED = Decimal("100")


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (ValueError, InvalidOperation):
        return None
    return number if number.is_finite() else None


def _fmt(number, places="0.01"):
    return None if number is None else format(number.quantize(Decimal(places), rounding=ROUND_HALF_UP), "f")


def _distance(price, base):
    return None if price is None or base is None or base <= 0 else (price / base - 1) * HUNDRED


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _comparison(close, ma):
    if close is None or ma is None or ma <= 0:
        return None
    return "above" if close > ma else "below" if close < ma else "equal"


def build_scenarios(symbol, quote, metrics, portfolio, *, now, dataset=None):
    """Return explainable, conditional watch/hold/accumulate/reduce scenarios.

    Monetary and percentage values are JSON-safe Decimal strings. ``budget`` and
    ``quantity`` intentionally stay unknown: cash, fees, settlement availability,
    and a user-confirmed risk budget are not scenario-engine assumptions.
    ``latestCompletedSession`` is separate from the V1 prior-session rule.
    """
    if now.tzinfo is None:
        raise ValueError("SCENARIO_CLOCK_REQUIRES_TIMEZONE")
    local = now.astimezone(BJ)
    day = local.date().isoformat()
    quote = quote or {}
    metrics = metrics or {}
    portfolio = portfolio or {}
    quality = quote.get("quality") or {}
    stamp = _stamp(quote.get("sourceTimestamp"))
    price = _number(quote.get("price"))
    time_of_day = local.time().replace(tzinfo=None)
    postclose = time_of_day >= time(15)
    window = "下个交易日研究条件（须先核验交易日历与新行情）" if postclose else "本次研究条件（须在有效盘中报价下重新核对）"
    continuous = time(9, 30) <= time_of_day <= time(11, 30) or time(13) <= time_of_day < time(14, 57)
    age = (now - stamp).total_seconds() if stamp else None
    today = bool(stamp and stamp.astimezone(BJ).date().isoformat() == day)
    identity = quote.get("symbol") == symbol
    unit_ok = quality.get("unitVerified") is True
    unadjusted = quote.get("adjustment", "none") == "none"
    safe_price = bool(identity and unit_ok and unadjusted and price is not None and price > 0)
    live = bool(safe_price and today and continuous and age is not None and 0 <= age <= 90
                and quality.get("status") == "intraday-verified"
                and quality.get("intradaySignalAllowed") is True)
    closing = bool(safe_price and today and postclose and stamp.astimezone(BJ).time().replace(tzinfo=None) >= time(15)
                   and stamp <= now and quality.get("status") == "closing-snapshot") if stamp else False
    dated = bool(safe_price and stamp and stamp <= now and quality.get("status") in {
        "closing-snapshot", "historical-snapshot", "intraday-verified"})

    latest = metrics.get("latestCompletedSession") or {}
    trend_date = latest.get("through") or metrics.get("through")
    close = _number(latest.get("close", metrics.get("previousClose")))
    ma = _number(latest.get("ma20", metrics.get("ma20")))
    trend = _comparison(close, ma)
    # An incomplete/future daily bar cannot become the model's complete session.
    trend_ok = bool(trend and isinstance(trend_date, str) and trend_date <= day
                    and (trend_date < day or postclose))
    if not trend_ok:
        trend = None
    distance = _distance(close, ma) if trend_ok else None
    quote_distance = _distance(price, ma) if trend_ok and dated else None
    ratio = _number(latest.get("volumeRatio", metrics.get("latestVsPrior19VolumeRatio")))
    if ratio is not None and ratio < 0:
        ratio = None
    drawdown = _number(latest.get("maxPriceDrawdownPct20d", metrics.get("maxPriceDrawdownPct")))
    volatility = _number(metrics.get("annualizedPriceVolatilityPct"))

    adjusted = latest.get("qfqTrendDetails") or metrics.get("qfqContext") or {}
    adjusted_date = adjusted.get("through")
    adjusted_close = _number(adjusted.get("close", adjusted.get("previousClose")))
    adjusted_ma = _number(adjusted.get("ma20"))
    adjusted_comparison = _comparison(adjusted_close, adjusted_ma)
    # Only compare directions calculated on matching complete dates. Merely
    # calling a series "qfq" does not certify dividends or reinvestment flows.
    adjusted_same_date = bool(trend_ok and adjusted_date == trend_date and adjusted_comparison)
    conflict = bool(adjusted_same_date and adjusted_comparison != trend)

    positions = [p for p in portfolio.get("positions", []) if p.get("symbol") == symbol]
    weights = [_number(p.get("weightPct")) for p in positions]
    if "symbolWeights" in portfolio:
        # Accounting aggregates exact unrounded market values before formatting
        # once per symbol. Summing rounded position percentages can yield 100.01
        # or move a holding across a concentration threshold by one cent.
        canonical = [w for w in portfolio.get("symbolWeights", []) if w.get("symbol") == symbol]
        weight = _number(canonical[0].get("weightPct")) if len(canonical) == 1 else None
    else:
        weight = sum(weights, Decimal(0)) if weights and all(w is not None and 0 <= w <= 100 for w in weights) else None
    if weight is not None and not 0 <= weight <= 100:
        weight = None
    quantities = [_number(p.get("quantity")) for p in positions]
    quantity = sum(quantities, Decimal(0)) if quantities and all(q is not None and q >= 0 for q in quantities) else None
    holding_current = bool(positions and portfolio.get("status") == "current-user-confirmed"
                           and all(p.get("currentHoldingConfirmed") is True for p in positions))
    known_cost = bool(positions and all(_number(p.get("costPrice")) is not None for p in positions))
    cash = _number(portfolio.get("availableCash"))
    concentrated = weight is not None and weight > Decimal("40")
    thin_volume = ratio is not None and ratio < Decimal("0.7")
    large_volume = ratio is not None and ratio >= Decimal("1.5")
    high_volatility = volatility is not None and volatility >= Decimal("30")
    deep_drawdown = drawdown is not None and drawdown <= Decimal("-20")

    reasons = []
    if not safe_price:
        reasons.append("标的身份、价格单位或未复权报价未通过核验，价格不能用于本次方向判断。")
    elif not live and not closing:
        reasons.append("没有本次有效盘中报价或当日收盘快照；已有日期价格仅作历史参考。")
    if not holding_current:
        reasons.append("缺少本标的当日确认持仓，不能将旧数量称为当前仓位。")
    if not trend_ok:
        reasons.append("缺少可核验的完整交易日MA20，不输出均线方向。")
    if conflict:
        reasons.append("同日未复权与前复权均线方向冲突；先核验除权分红与口径，暂停加减仓方向研究。")
    elif not adjusted_same_date:
        reasons.append("缺少同日期前复权对照；原始价格均线可能受除权分红影响。")
    reasons.append("公开网站报价不等于独立交易所数据源交叉认证；不提供统计置信概率。")

    v1_close = _number(metrics.get("previousClose"))
    v1_ma = _number(metrics.get("ma20"))
    v1_comparison = _comparison(v1_close, v1_ma)
    v1_text = (f"原V1：只比较{metrics.get('through')}的前一完整交易日收盘{_fmt(v1_close, '0.0001')}与含该日MA20 {_fmt(v1_ma, '0.0001')}，结果{v1_comparison}；不是均线交叉。"
               if v1_comparison else "原V1前收盘对MA20：数据不足。")
    cross = metrics.get("cross") if metrics.get("cross") in {"up", "down", "none"} else None
    cross_text = {"up": "独立交叉对照：前一日不高于MA20、本日高于MA20，形成历史向上交叉。",
                  "down": "独立交叉对照：前一日不低于MA20、本日低于MA20，形成历史向下交叉。",
                  "none": "独立交叉对照：没有新的交叉；站在均线上方不等于新金叉。"}.get(cross, "独立交叉对照：完整两日数据不足。")
    basis = [v1_text, cross_text]
    if trend_ok:
        basis.append(f"最新完整日{trend_date}收盘{_fmt(close, '0.0001')}，MA20 {_fmt(ma, '0.0001')}，距离{_fmt(distance)}%；{('在均线上方' if trend == 'above' else '在均线下方' if trend == 'below' else '与均线相等')}。")
    if ratio is not None:
        basis.append(f"完整日成交量/此前19个完整交易日均量={_fmt(ratio)}倍；{'明显放量' if large_volume else '明显缩量' if thin_volume else '未触及本研究放缩量阈值'}。盘中累计量不直接与全天量比较。")
    if weight is not None:
        basis.append(f"占已导入证券市值{_fmt(weight)}%；{'超过40%集中度观察阈值' if concentrated else '未超过40%集中度观察阈值'}，该分母不含未知现金或其他资产。")
    risk = ["情景只用于人工研究，不是下单指令。", "价格路径回测与前复权趋势均不等于已核验分红、税费和再投资后的完整投资收益。",
            "未取得完整逐次分红现金流，长江电力分红收益及ETF分配收益不能据价格回测补造。",
            "实际佣金、最低收费、税费和滑点未由用户确认，不给出假精确盈亏或净收益。"]
    if drawdown is not None:
        risk.append(f"{('最新20个完整交易日' if latest.get('maxPriceDrawdownPct20d') is not None else '可用历史样本')}原始价格最大回撤{_fmt(drawdown)}%；不等于持仓实际亏损或完整含息回撤。")
    if volatility is not None:
        risk.append(f"历史日收益年化价格波动率{_fmt(volatility)}%；仅为样本估计，不保证未来风险。")
    if not known_cost:
        risk.append("持仓成本缺失或未核对券商口径，不推算回本价和净盈利。")
    if cash is None:
        risk.append("可用现金未知，不计算买入预算。")

    # A historical quote remains a dated fact, never authorization to assess a
    # present holding's directional conditions. Both the quote and complete-day
    # model must be usable for this run, not merely present in the payload.
    blocked = not (live or closing) or not trend_ok or conflict
    holding_note = "需先核对当日持仓" if not holding_current else "当日用户确认持仓已用于研究"
    watch_trigger = ("先修复数据质量/复权冲突，再评估方向" if blocked else
                     "没有确定的新增交叉或明确风险预算时，保留现状并观察下一完整交易日" if cross != "up" else
                     "已有历史向上交叉也需核对成交量、集中度和持仓，不机械追买")
    hold_trigger = ("先补齐有效行情及同口径日线" if blocked else
                    f"若后续完整日继续守住MA20且风险预算未超限，可继续持有观察；{holding_note}" if trend == "above" else
                    f"当前完整日未站稳MA20，持有需结合分红、期限和承受回撤能力；{holding_note}")
    accumulate_trigger = ("先排除数据/复权冲突，不讨论加仓方向" if blocked else
                          "后续完整日收盘在同口径MA20上方，量能改善且集中度回到预算内，再研究分批投入；不以低于成本为加仓理由")
    reduce_trigger = ("先排除数据/复权冲突，不讨论减仓方向" if blocked else
                      "后续完整日继续弱于同口径MA20，或集中度超过个人上限，再研究分批降风险；不能仅凭单日波动卖出")
    common_constraints = ["600900按A股、515450按境内股票ETF的T+1约束核对；不是所有ETF都适用同一回转规则，可卖数量须以券商实际可用数量为准。",
                          "买入按100股/份整数手核对；零股卖出、可用资金和费用遵守券商规则。",
                          "缺少用户确认的风险预算、费率与可卖数量，不提供股数、金额或自动止损订单。"]
    scenarios = [
        {"id": "watch", "label": "观察、不操作", "status": "preferred" if blocked or trend != "above" else "available",
         "trigger": watch_trigger, "basis": list(basis), "risks": [*reasons, *risk], "budget": None, "quantity": None},
        {"id": "hold", "label": "持有研究", "status": "blocked" if blocked else "conditional-fit" if holding_current and trend == "above" else "conditional",
         "trigger": hold_trigger, "basis": list(basis), "risks": [*risk, *(["集中度偏高，持有不等于可以继续加仓。"] if concentrated else [])], "budget": None, "quantity": None},
        {"id": "accumulate", "label": "分批加仓研究", "status": "blocked" if blocked or not holding_current or cash is None or not known_cost else "condition-not-met" if trend != "above" or concentrated or thin_volume else "conditional",
         "trigger": accumulate_trigger, "basis": list(basis), "risks": [*risk, *common_constraints, *(["现有集中度已超过研究阈值，不因趋势向上增加单标的风险。"] if concentrated else []), *(["历史波动或回撤较大，先确定个人可承受损失，不扩大研究仓位。"] if high_volatility or deep_drawdown else [])], "budget": None, "quantity": None},
        {"id": "reduce", "label": "分批减仓研究", "status": "blocked" if blocked or not holding_current else "conditional-fit" if trend == "below" or concentrated else "conditional",
         "trigger": reduce_trigger, "basis": list(basis), "risks": [*risk, *common_constraints, "减仓研究也需考虑分红除权和费用；均线下方不是必然下跌。"], "budget": None, "quantity": None},
    ]
    if not blocked and not adjusted_same_date:
        # Price-only evidence can describe a position, but cannot elevate a
        # directional scenario to a usable action condition across a possible
        # corporate-action discontinuity.
        for scenario in scenarios[1:]:
            if scenario["status"] != "blocked":
                scenario["status"] = "conditional-not-actionable"
            scenario["risks"].append("尚无同日同口径复权对照，只有价格事实；先核验除权分红，不据此执行加减仓。")
    name = NAMES.get(symbol, symbol)
    if blocked:
        summary = f"{name}：观察、不操作；{'复权方向冲突，先核验除权与分红' if conflict else '证据尚不足以形成方向判断'}。"
    elif not holding_current:
        summary = f"{name}：已有带日期行情和趋势研究，但当前真实持仓未确认，先观察。"
    elif concentrated:
        summary = f"{name}：{'趋势仍在MA20上方' if trend == 'above' else '趋势未站稳MA20'}，单标的占已导入证券{_fmt(weight)}%；优先核对集中度，研究降风险，不自动减仓。"
    elif trend == "above":
        summary = f"{name}：完整日收盘高于MA20 {_fmt(distance)}%，可继续持有观察；{'历史向上交叉仅供进一步核查' if cross == 'up' else '没有新的交叉，不必为交易而交易'}。"
    else:
        summary = f"{name}：完整日收盘距MA20 {_fmt(distance)}%，{'且明显放量，需关注下行风险' if large_volume and trend == 'below' else '先观察能否重新站稳均线'}；减仓仅为待核对的条件情景。"
    if postclose:
        summary += " 收盘后内容是下个交易日研究条件，不是今天可执行的实时信号。"
    elif not live:
        summary += " 盘中行情未验证，不提供今天实时操作信号。"
    return {
        "summary": summary,
        "evidenceQuality": {"level": "insufficient" if blocked else "dated-research" if not live else "conditional-research",
                            "reasons": reasons, "currentIntradayVerified": live, "closingSnapshot": closing,
                            "independentFeedsVerified": False, "statisticalConfidence": None},
        "facts": {"symbol": symbol, "quoteAsOf": quote.get("sourceTimestamp"), "quoteStatus": quality.get("status"),
                  "price": str(price) if dated else None, "trendThrough": trend_date if trend_ok else None,
                  "completedClose": _fmt(close, "0.0001") if trend_ok else None,
                  "ma20": _fmt(ma, "0.0001") if trend_ok else None, "completedCloseVsMa20Pct": _fmt(distance),
                  "unadjustedQuoteVsMa20Pct": _fmt(quote_distance), "completedDayVolumeRatio": _fmt(ratio),
                  "historicalMaxPriceDrawdownPct": _fmt(drawdown), "annualizedPriceVolatilityPct": _fmt(volatility),
                  "importedSecuritiesWeightPct": _fmt(weight), "quantityFromConfirmedSnapshot": str(quantity) if holding_current and quantity is not None else None,
                  "holdingCurrent": holding_current, "availableCashKnown": cash is not None, "costKnown": known_cost},
        "modelConditions": {"window": window, "postClose": postclose, "originalV1Rule": v1_text,
                            "independentCross": cross_text, "adjustmentConflict": conflict,
                            "adjustedSameDateComparison": adjusted_comparison if adjusted_same_date else None,
                            "priceOnlyNotTotalReturn": True, "automaticOrdersAllowed": False,
                            "researchThresholds": {"concentrationPct": "40", "lowVolumeRatio": "0.70", "highVolumeRatio": "1.50", "highAnnualizedVolatilityPct": "30", "deepPriceDrawdownPct": "-20"},
                            "thresholdNote": "公开的描述性研究阈值，未做参数寻优，不代表用户授权的仓位或止损线。"},
        "scenarios": scenarios,
    }
