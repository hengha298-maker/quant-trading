"""Decimal-only, read-only valuation of explicitly imported account snapshots.

Broker/export amounts and a public-quote revaluation answer different questions.
Neither is silently substituted for the other. This module performs no I/O and
does not infer an account's missing cash, cost, fees or dividend treatment.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


BJ = timezone(timedelta(hours=8))
CENT = Decimal("0.01")
HUNDRED = Decimal(100)
COST_BASIS = "导入成本价×持有数量；券商摊薄、分红及历史费用口径尚未核验。"
FEE_BASIS = "报价重估差额未另扣未来卖出佣金、印花税及其他费用；不能作为可兑现净收益。"
VALUATION_BASIS = "以核验通过的人民币未复权报价×导入数量重估；不累加券商导入市值。"
ROUNDING_BASIS = "内部使用十进制未舍入金额汇总，金额显示时四舍五入到分，占比显示两位小数。"
DIFFERENCE_BASIS = "差额=报价重估差额－导入盈亏；报价时点、成本价精度、费用或分红口径可能不同，原因尚未核验。"


def _number(value, *, nonnegative=False):
    # Floats and bools cannot introduce a silently rounded financial input.
    if type(value) not in (str, int, Decimal):
        return None
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or abs(result) > Decimal("1e30"):
        return None
    if nonnegative and result < 0:
        return None
    return result


def _money(value):
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f") if value is not None else None


def _source_day(value):
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(BJ).date().isoformat() if stamp.tzinfo else None
    except ValueError:
        return None


def _snapshot_key(value):
    """Different explicit times are not a single simultaneous broker snapshot."""
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return "date:" + date.fromisoformat(value).isoformat()
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return "time:" + stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else None
    except ValueError:
        return None


def _quote_price(quote, symbol, day):
    if not isinstance(quote, dict):
        return None
    quality = quote.get("quality", {})
    if (not isinstance(quality, dict)
            or quality.get("status") not in {"intraday-verified", "closing-snapshot"}
            or quality.get("unitVerified") is not True
            or quality.get("status") == "intraday-verified" and quality.get("intradaySignalAllowed") is False
            or quote.get("symbol", symbol) != symbol
            or quote.get("currency", "CNY") != "CNY"
            or quote.get("priceUnit", "CNY-per-share") not in {"CNY-per-share", "CNY-per-unit"}
            or quote.get("adjustment", "none") != "none"):
        return None
    try:
        stamp = datetime.fromisoformat(quote["sourceTimestamp"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.astimezone(BJ).date().isoformat() != day:
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    price = _number(quote.get("price"), nonnegative=True)
    return price if price is not None and price > 0 else None


def _complete_sum(rows, key):
    values = [row[key] for row in rows]
    return sum(values, Decimal(0)) if values and all(value is not None for value in values) else None


def _reported_total(rows, key):
    """Do not fabricate one portfolio snapshot out of incompatible broker times."""
    stamps = {_snapshot_key(row["position"]["reportedAsOf"]) for row in rows}
    if not rows or None in stamps or len(stamps) != 1:
        return None
    return _complete_sum(rows, key)


def _group_totals(rows):
    market = _complete_sum(rows, "value")
    cost = _complete_sum(rows, "cost")
    computed = market - cost if market is not None and cost is not None else None
    reported = _reported_total(rows, "reportedPnl")
    reported_market = _reported_total(rows, "reportedMarketValue")
    return {
        "marketValue": _money(market), "costValue": _money(cost),
        "pnl": _money(computed), "computedPnl": _money(computed),
        "reportedPnl": _money(reported), "reportedMarketValue": _money(reported_market),
        "pnlDifference": _money(computed - reported) if computed is not None and reported is not None else None,
        "knownMarketValueSubtotal": _money(sum((r["value"] for r in rows if r["value"] is not None), Decimal(0))),
        "knownMarketValueCount": sum(r["value"] is not None for r in rows),
        "positionCount": len(rows),
        "reportedTotalsComparable": reported is not None,
    }


def build_portfolio(holdings, quotes, day):
    """Preserve ``engine.portfolio`` keys and add explicit accounting provenance.

    ``quotes`` must come through the market adapter's unit/date/freshness checks.
    A same-day quote is an observation, not evidence that it is live at view time;
    cached report freshness remains the caller's separate responsibility.
    """
    with localcontext() as context:
        # Bound inputs above can multiply to 1e60; preserve cent formatting even
        # for malformed-but-finite large inputs before the import layer rejects.
        context.prec = 100
        return _build(holdings, quotes, day)


def _build(holdings, quotes, day):
    common = {
        "valuationBasis": VALUATION_BASIS, "costBasis": COST_BASIS,
        "feeBasis": FEE_BASIS, "roundingBasis": ROUNDING_BASIS,
        "pnlDifferenceBasis": DIFFERENCE_BASIS,
        "pnlLabel": "报价重估差额（非券商浮盈亏）", "reportedPnlLabel": "导入盈亏（原值）",
        "weightBasis": "only-imported-securities-excludes-cash-and-other-assets",
        "knownMarketValueSubtotalLabel": "仅已通过行情校验的持仓估值小计，不代表完整账户资产",
        "reportedTotalBasis": "仅对所有行均已提供、且声明快照时间一致的导入值求和；不证明同一实时估值。",
    }
    if not holdings:
        return {**common, "status": "missing", "asOf": None, "positions": [],
                "availableCash": None, "marketValue": None, "pnl": None,
                "computedPnl": None, "reportedPnl": None, "reportedMarketValue": None,
                "pnlDifference": None, "costValue": None, "concentration": None,
                "knownMarketValueSubtotal": None, "knownMarketValueCount": 0,
                "accounts": [], "missingBrokers": [], "symbolWeights": [],
                "warnings": ["未导入当前真实持仓。历史记录没有被当作当前账户。"]}

    stamp = holdings.get("asOf", "")
    account_metadata = {a.get("source", {}).get("broker"): a for a in holdings.get("accounts", [])}
    records = []
    positions = []
    for original in holdings.get("positions", []):
        quote = quotes.get(original["symbol"])
        price = _quote_price(quote, original["symbol"], day)
        quantity = _number(original.get("quantity"), nonnegative=True)
        if quantity is not None and quantity != quantity.to_integral_value():
            quantity = None
        imported_cost = _number(original.get("costPrice"), nonnegative=True)
        value = price * quantity if price is not None and quantity is not None else None
        cost = imported_cost * quantity if imported_cost is not None and quantity is not None else None
        computed = value - cost if value is not None and cost is not None else None
        reported = _number(original.get("reportedPnl"))
        source_as_of = original.get("holdingAsOf", stamp)
        account = account_metadata.get(original.get("broker"), {})
        position = {
            **original, "quote": quote, "marketValue": _money(value),
            "costValue": _money(cost), "pnl": _money(computed), "computedPnl": _money(computed),
            "weightPct": None, "symbolWeightPct": None,
            "currentHoldingConfirmed": _source_day(source_as_of) == day,
            "reportedAsOf": source_as_of,
            "reportedSourceKind": account.get("source", {}).get("kind"),
            "pnlDifference": _money(computed - reported) if computed is not None and reported is not None else None,
            "costBasis": COST_BASIS, "feeBasis": FEE_BASIS,
            "pnlDifferenceBasis": DIFFERENCE_BASIS,
            "valuationStatus": "dated-quote-revaluation" if value is not None else "quote-or-quantity-unusable",
        }
        # reportedPnl/reportedMarketValue remain the exact imported values above.
        positions.append(position)
        records.append({"position": position, "value": value, "cost": cost,
                        "reportedPnl": reported,
                        "reportedMarketValue": _number(original.get("reportedMarketValue"), nonnegative=True)})

    totals = _group_totals(records)
    all_quotes = all(row["value"] is not None for row in records)
    all_costs = all(row["cost"] is not None for row in records)
    total = sum((row["value"] for row in records if row["value"] is not None), Decimal(0))
    weights = []
    concentration = None
    if all_quotes and total > 0:
        by_symbol = {}
        for row in records:
            symbol = row["position"]["symbol"]
            by_symbol[symbol] = by_symbol.get(symbol, Decimal(0)) + row["value"]
            row["position"]["weightPct"] = _money(row["value"] / total * HUNDRED)
        symbol_weights = {symbol: value / total * HUNDRED for symbol, value in by_symbol.items()}
        for row in records:
            row["position"]["symbolWeightPct"] = _money(symbol_weights[row["position"]["symbol"]])
        concentration = max(symbol_weights.values())
        weights = [{"symbol": symbol, "marketValue": _money(by_symbol[symbol]), "weightPct": _money(weight)}
                   for symbol, weight in sorted(symbol_weights.items())]

    accounts = []
    brokers = list(account_metadata)
    brokers.extend(p.get("broker") for p in positions if p.get("broker") and p.get("broker") not in brokers)
    # Keep one account cash amount per broker, never one copy per security row.
    brokers = list(dict.fromkeys(brokers))
    for broker in brokers:
        account = account_metadata.get(broker, {})
        rows = [row for row in records if row["position"].get("broker") == broker]
        raw_cash = account.get("availableCash")
        cash = _number(raw_cash, nonnegative=True)
        entry = {"broker": broker, "asOf": account.get("asOf", stamp),
                 "availableCash": raw_cash if cash is not None else None,
                 "sourceKind": account.get("source", {}).get("kind"), **_group_totals(rows),
                 "costBasis": COST_BASIS, "feeBasis": FEE_BASIS}
        accounts.append(entry)

    missing = list(holdings.get("missingBrokers", []))
    # The aggregate input already contains exactly-once account cash. Recompute
    # from account snapshots when available to defend against row duplication.
    cash_values = [_number(a["availableCash"], nonnegative=True) for a in accounts]
    if accounts:
        cash = _money(sum(cash_values, Decimal(0))) if not missing and all(v is not None for v in cash_values) else None
    else:
        cash = holdings.get("availableCash") if _number(holdings.get("availableCash"), nonnegative=True) is not None and not missing else None
    current = _source_day(stamp) == day and all(p["currentHoldingConfirmed"] for p in positions) and not missing
    warnings = []
    if _source_day(stamp) != day or any(not p["currentHoldingConfirmed"] for p in positions):
        warnings.append("持仓日期不是今天或不同账户日期不一致；仅为导入快照按报价重估，不能称为当前真实持仓。")
    if not all_quotes:
        warnings.append("部分报价缺失、日期/单位/标的未通过核验，总市值、总体占比和集中度保持未知；已知小计不等于总额。")
    if not all_costs:
        warnings.append("部分成本未知，总报价重估差额保持未知，不将未知成本当成零。")
    if missing:
        warnings.append("尚未提供的券商：" + "、".join(missing) + "；账户总体仓位和现金并不完整。")
    if cash is None:
        warnings.append("可用资金未完整提供，保持未知；证券市值占比不代表家庭资产仓位，也不代表可买入额度。")
    if totals["reportedPnl"] is None and any(r["reportedPnl"] is not None for r in records):
        warnings.append("导入盈亏未全部提供或账户快照时间不一致，合计保持未知；各行导入原值仍完整保留。")
    if any(p["pnlDifference"] not in (None, "0.00", "-0.00") for p in positions):
        warnings.append(DIFFERENCE_BASIS)
    if concentration is not None and concentration > Decimal(40):
        warnings.append("单一标的占已导入证券市值超过40%（跨券商同代码已合并），需研究集中度；40%为研究阈值，不是买卖指令。")
    warnings.extend([COST_BASIS, FEE_BASIS])
    return {**common, **totals,
            "status": "current-user-confirmed" if current else "partial-accounts" if missing else "stale-snapshot",
            "asOf": stamp, "positions": positions, "availableCash": cash,
            "concentration": _money(concentration), "symbolWeights": weights,
            "accounts": accounts, "missingBrokers": missing, "warnings": warnings}
