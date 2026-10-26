"""Deterministic daily-bar research backtests; no network, database or V1 imports.

Cash, costs and equity are integer CNY cents. Per-share execution prices retain
Decimal fractions of one cent (necessary for ETF price ticks). All decisions
execute at the next eligible session's open, never at the signal close.
Returns chain the overnight mark before funding and the intraday period after
opening funding, so new cash cannot dilute a preceding overnight price gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
import re

D = Decimal
SHANGHAI = timezone(timedelta(hours=8))
STRATEGIES = ("buy_hold", "v1_ma20", "ma20_cross", "periodic")
DEFAULTS = {
    "initialCash": "10000000", "commissionBps": 3, "slippageBps": 5,
    "stampDutyBps": 5, "minCommission": "500", "maxPositionBps": 10000,
    "buyBudgetBps": 10000, "periodicEvery": 20, "periodicContribution": "100000",
}


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期必须为YYYY-MM-DD")
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("日期无效")
    return value


def _money(value, name, positive=False):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9]\d{0,59}", value):
        raise ValueError(f"{name}必须为至多60位非负整数分字符串")
    result = int(value)
    if positive and result == 0:
        raise ValueError(f"{name}必须大于零")
    return result


def _cents(value):
    return int(value.quantize(D(1), rounding=ROUND_HALF_UP))


def _decimal(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d{0,29})(?:\.\d{1,12})?", value):
        raise ValueError("价格必须是规范的正十进制文本")
    result = D(value)
    if result <= 0:
        raise ValueError("价格必须大于零")
    return result


@dataclass(frozen=True)
class _Bar:
    date: str
    open: Decimal
    close: Decimal
    volume: int
    published: datetime | None


def _prepare(dataset):
    if not isinstance(dataset, dict) or dataset.get("adjustment") != "none":
        raise ValueError("仅支持明确的未复权none价格口径")
    if dataset.get("currency") != "CNY" or dataset.get("priceUnit") != "CNY-per-share":
        raise ValueError("必须明确CNY及CNY-per-share单位")
    if dataset.get("kind") not in {"stock", "etf"}:
        raise ValueError("仅支持股票或ETF")
    if not re.fullmatch(r"\d{6}\.(SH|SZ)", str(dataset.get("symbol", ""))):
        raise ValueError("标的编号无效")
    quality = dataset.get("quality", {})
    if not isinstance(quality, dict) or quality.get("status") not in {"validated", "synthetic"}:
        raise ValueError("blocked或未核验数据禁止运行回测")
    if dataset["kind"] == "etf" and quality.get("unitVerified") is not True:
        raise ValueError("ETF价格单位尚未核验，禁止回测")
    calendar = dataset.get("calendar", {})
    if not isinstance(calendar, dict) or calendar.get("name") != "XSHG" or not isinstance(calendar.get("sessions"), list):
        raise ValueError("缺少完整XSHG交易日历")
    sessions = [_date(x) for x in calendar["sessions"]]
    if sessions != sorted(set(sessions)):
        raise ValueError("XSHG交易日历重复或未排序")
    raw = dataset.get("bars")
    if not isinstance(raw, list) or len(raw) < 22:
        raise ValueError("MA20比较至少需要21日预热和1个执行交易日")
    bars = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("日线无效")
        day = _date(item.get("date"))
        opening, high, low, close = [_decimal(item.get(k)) for k in ("open", "high", "low", "close")]
        if low > min(opening, close) or high < max(opening, close) or high < low:
            raise ValueError("OHLC价格范围无效")
        volume = item.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
            raise ValueError("成交量必须是非负整数股")
        published = item.get("publishedAt")
        if published is not None:
            if not isinstance(published, str):
                raise ValueError("发布时间格式无效")
            published = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if published.tzinfo is None or published < datetime.combine(date.fromisoformat(day), time(15), SHANGHAI):
                raise ValueError("完整日线发布时间必须带时区且不早于当日收盘")
        bars.append(_Bar(day, opening, close, volume, published))
    days = [x.date for x in bars]
    if days != sorted(set(days)):
        raise ValueError("日线日期重复或未排序")
    expected = [s for s in sessions if days[0] <= s <= days[-1]]
    if expected != days:
        raise ValueError("日线与XSHG交易日历不一致，存在缺失或非交易日；不填造价格")
    return bars, quality


def _configuration(config, bars):
    if config is not None and not isinstance(config, dict):
        raise ValueError("回测参数必须为对象")
    cfg = {**DEFAULTS, **(config or {})}
    for key in ("initialCash", "minCommission", "periodicContribution"):
        cfg[key] = _money(cfg[key], key, positive=key == "initialCash")
    for key in ("commissionBps", "slippageBps", "stampDutyBps", "maxPositionBps", "buyBudgetBps"):
        if isinstance(cfg[key], bool) or not isinstance(cfg[key], int) or not 0 <= cfg[key] <= 10000:
            raise ValueError(f"{key}必须为0–10000整数基点")
    if cfg["slippageBps"] == 10000:
        raise ValueError("滑点必须小于10000基点")
    if isinstance(cfg["periodicEvery"], bool) or not isinstance(cfg["periodicEvery"], int) or not 1 <= cfg["periodicEvery"] <= 10000:
        raise ValueError("periodicEvery必须为1–10000交易日")
    partitions = cfg.get("partitions")
    if partitions is None and any(k in cfg for k in ("train", "validation", "test")):
        partitions = {k: cfg.get(k) for k in ("train", "validation", "test")}
    if partitions is not None:
        if not isinstance(partitions, dict) or set(partitions) != {"train", "validation", "test"}:
            raise ValueError("必须提供固定日期的train/validation/test三分区")
        previous = None
        for key in ("train", "validation", "test"):
            part = partitions[key]
            if not isinstance(part, dict) or set(part) != {"from", "to"}:
                raise ValueError("分区必须包含from/to")
            begin, end = _date(part["from"]), _date(part["to"])
            if begin > end or previous is not None and begin <= previous:
                raise ValueError("分区必须按时间严格先后且不重叠")
            previous = end
    start = cfg.get("start") or (partitions["train"]["from"] if partitions else bars[21].date)
    end = cfg.get("end") or (partitions["test"]["to"] if partitions else bars[-1].date)
    _date(start); _date(end)
    if start > end:
        raise ValueError("共同区间起止日期倒置")
    indexes = [i for i in range(21, len(bars)) if start <= bars[i].date <= end]
    if not indexes:
        raise ValueError("预热后没有共同回测交易日")
    if partitions is not None:
        if cfg.get("start") not in (None, partitions["train"]["from"]) or cfg.get("end") not in (None, partitions["test"]["to"]):
            raise ValueError("显式区间必须与固定分区train.from/test.to一致")
        if any(not any(part["from"] <= bars[i].date <= part["to"] for i in indexes) for part in partitions.values()):
            raise ValueError("固定分区在共同可用区间内没有交易日")
    else:
        # Date boundaries are fixed once, before running any strategy. No search
        # or strategy-dependent selection occurs.
        n = len(indexes); a = max(1, n * 3 // 5); b = max(a + 1, n * 4 // 5)
        partitions = {}
        for name, group in zip(("train", "validation", "test"), (indexes[:a], indexes[a:b], indexes[b:])):
            partitions[name] = {"from": bars[group[0]].date, "to": bars[group[-1]].date} if group else None
    return cfg, indexes, partitions


def _available(bar, execution_day):
    # Unknown publication time is explicitly assumed available next session.
    opening = datetime.combine(date.fromisoformat(execution_day), time(9, 30), SHANGHAI)
    return bar.date < execution_day and (bar.published is None or bar.published <= opening)


def _ma_decision(bars, index, crossing=False):
    needed = 21 if crossing else 20
    window = bars[index-needed:index]
    if len(window) != needed or any(not _available(bar, bars[index].date) for bar in window):
        return None
    current = window[-1].close - sum((b.close for b in window[-20:]), D(0)) / 20
    if not crossing:
        return "BUY" if current > 0 else "SELL" if current < 0 else "HOLD"
    previous = window[-2].close - sum((b.close for b in window[-21:-1]), D(0)) / 20
    if current > 0 and previous <= 0:
        return "BUY"
    if current < 0 and previous >= 0:
        return "SELL"
    return "HOLD"


def _signal_basis(strategy, bars, index, decision):
    """Auditable historical inputs, separate from the day's fill feasibility."""
    window = bars[index-20:index]
    previous = window[-1]
    average = sum((bar.close for bar in window), D(0)) / 20
    relation = ">" if previous.close > average else "<" if previous.close < average else "="
    rule = "BUY" if relation == ">" else "SELL" if relation == "<" else "HOLD"
    basis = (f"历史信号日{previous.date}：前收盘{format(previous.close, 'f')}元 "
        f"{relation} MA20 {format(average, 'f')}元；20日区间{window[0].date}至{window[-1].date}"
        f"（含信号日，共20条完整交易日线）；连续比较结果{rule}。")
    if strategy == "ma20_cross":
        older = bars[index-21:index-1]
        older_average = sum((bar.close for bar in older), D(0)) / 20
        older_close = older[-1].close
        older_relation = ">" if older_close > older_average else "<" if older_close < older_average else "="
        basis += (f"此前{older[-1].date}收盘{format(older_close, 'f')}元 {older_relation} "
            f"MA20 {format(older_average, 'f')}元；独立交叉结果{decision or '不可用'}。")
    elif strategy != "v1_ma20":
        basis += f"{strategy}不以MA20筛选，以上仅为历史参考上下文；本策略指令{decision or '不可用'}。"
    if decision is None:
        basis += "前序日线或发布时间不满足开盘可用条件，已跳过，以上文件数值不能视为当时已发布信号。"
    basis += (f"模拟撮合日{bars[index].date}，文件数据截至{bars[-1].date}；signal.asOf是上述前一研究日，"
        "不同于文件through日期，也不是今日或实时信号。")
    return basis


class _Portfolio:
    def __init__(self, cfg, kind, symbol):
        self.cfg, self.kind, self.symbol = cfg, kind, symbol
        self.cash = cfg["initialCash"]
        self.quantity = 0
        self.lots = []
        self.trades = []

    def _quote(self, side, quantity, opening):
        direction = 1 if side == "BUY" else -1
        fill = opening * (1 + direction * D(self.cfg["slippageBps"]) / 10000)
        gross = _cents(fill * quantity * 100)
        commission = max(self.cfg["minCommission"], _cents(D(gross) * self.cfg["commissionBps"] / 10000))
        tax = _cents(D(gross) * self.cfg["stampDutyBps"] / 10000) if side == "SELL" and self.kind == "stock" else 0
        slippage = _cents(abs(fill - opening) * quantity * 100)
        return fill, gross, commission, tax, slippage

    def buy(self, day, opening, budget):
        budget = min(self.cash, budget)
        high = int((D(budget) / (opening * (1 + D(self.cfg["slippageBps"]) / 10000) * 10000)).to_integral_value(rounding=ROUND_FLOOR))
        low, best = 1, 0
        while low <= high:
            middle = (low + high) // 2
            _, gross, commission, _, _ = self._quote("BUY", middle * 100, opening)
            remaining = self.cash - gross - commission
            position = _cents(opening * (self.quantity + middle * 100) * 100)
            affordable = gross + commission <= budget and remaining >= 0
            within_cap = position * 10000 <= (remaining + position) * self.cfg["maxPositionBps"]
            if affordable and within_cap:
                best = middle; low = middle + 1
            else:
                high = middle - 1
        if best:
            self._execute("BUY", day, best * 100, opening)

    def sell(self, day, opening):
        quantity = sum(qty for bought, qty in self.lots if bought < day) // 100 * 100
        if quantity:
            _, gross, commission, tax, _ = self._quote("SELL", quantity, opening)
            if self.cash + gross - commission - tax >= 0:
                self._execute("SELL", day, quantity, opening)

    def _execute(self, side, day, quantity, opening):
        fill, gross, commission, tax, slippage = self._quote(side, quantity, opening)
        delta = -gross - commission if side == "BUY" else gross - commission - tax
        self.cash += delta
        if side == "BUY":
            self.quantity += quantity; self.lots.append((day, quantity))
        else:
            self.quantity -= quantity
            remaining = quantity; lots = []
            for bought, qty in self.lots:
                sold = min(remaining, qty) if bought < day else 0
                remaining -= sold
                if qty > sold:
                    lots.append((bought, qty-sold))
            self.lots = lots
        self.trades.append({"date": day, "symbol": self.symbol, "side": side,
            "quantity": quantity, "price": format(fill * 100, "f"),
            "gross": str(gross), "totalCost": str(commission + tax + slippage),
            "commission": str(commission), "stampDuty": str(tax), "slippage": str(slippage),
            "cashDelta": str(delta)})


def _bps(value):
    return _cents(value * 10000)


def _metrics(curve, trades, initial, before=None):
    if not curve:
        return None
    previous = D(before if before is not None else initial)
    nav = peak = D(1); drawdown = D(0); returns = []
    for row in curve:
        if "openingEquityBeforeFlow" not in row:
            raise ValueError("CURVE_OPENING_EQUITY_REQUIRED_FOR_EXACT_TWR")
        opening = D(row["openingEquityBeforeFlow"])
        funded_opening = opening + D(row["externalFlow"])
        current = D(row["equity"])
        if opening < 0 or funded_opening < 0 or current < 0:
            raise ValueError("TWR_NEGATIVE_EQUITY_NOT_SUPPORTED")
        if previous == 0 and opening != 0 or funded_opening == 0 and current != 0:
            raise ValueError("TWR_ZERO_CAPITAL_NONZERO_VALUE")
        # Cash arrives after the opening mark. It must not be included in the
        # denominator of an overnight return earned by existing shareholders.
        overnight = opening / previous if previous else D(1)
        intraday = current / funded_opening if funded_opening else D(1)
        change = overnight * intraday - 1
        returns.append(change); nav *= 1 + change; peak = max(peak, nav)
        drawdown = max(drawdown, (peak-nav)/peak if peak else D(0))
        previous = current
    annualized = None
    if nav == 0:
        annualized = -10000
    elif nav > 0:
        annual_factor = nav ** (D(252) / len(curve))
        if abs(annual_factor-1) <= D("100000000"):
            annualized = _bps(annual_factor-1)
    volatility = None
    if len(returns) > 1:
        mean = sum(returns)/len(returns)
        variance = sum(((r-mean)**2 for r in returns), D(0))/(len(returns)-1)
        volatility = _bps(variance.sqrt()*D(252).sqrt())
    return {"totalReturnBps": _bps(nav-1), "annualizedReturnBps": annualized,
        "maxDrawdownBps": _bps(drawdown), "volatilityBps": volatility,
        "tradeCount": len(trades), "cost": str(sum(int(t["totalCost"]) for t in trades)),
        "externalContributions": str(sum(int(r["externalFlow"]) for r in curve)),
        "endingEquity": curve[-1]["equity"], "tradingDays": len(curve),
        "returnMethod": "subperiod-TWR-overnight-then-intraday-opening-flows",
        "flowTiming": "after-opening-mark-before-orders"}


def _run(strategy, bars, indexes, cfg, dataset, quality, partitions):
    portfolio = _Portfolio(cfg, dataset["kind"], dataset["symbol"])
    curve = []; orders = []; skipped = []; has_bought = False; last_decision = "HOLD"
    periodic_carry = 0
    for offset, index in enumerate(indexes):
        bar = bars[index]
        scheduled = offset % cfg["periodicEvery"] == 0
        flow = cfg["periodicContribution"] if offset > 0 and scheduled else 0
        opening_equity_before_flow = portfolio.cash + _cents(bar.open * portfolio.quantity * 100)
        portfolio.cash += flow
        if strategy == "periodic" and scheduled:
            # The first allowance reserves existing initial cash; subsequent
            # allowances match the common external funding schedule. A reserve
            # is not another deposit and cannot itself change equity.
            periodic_carry += cfg["periodicContribution"]
        decision = "HOLD"
        if not _available(bars[index-1], bar.date):
            decision = None
        elif strategy in {"v1_ma20", "ma20_cross"}:
            decision = _ma_decision(bars, index, strategy == "ma20_cross")
        elif strategy == "buy_hold":
            decision = "BUY" if not has_bought or flow else "HOLD"
        elif scheduled:
            decision = "BUY"
        if decision is None:
            skipped.append(bar.date)
        elif decision in {"BUY", "SELL"}:
            budget = portfolio.cash * cfg["buyBudgetBps"] // 10000
            if strategy == "periodic":
                budget = min(budget, periodic_carry)
            # Instruction is generated before consulting this day's realized
            # volume. Volume is never an input to _ma_decision or the signal.
            order = {"date": bar.date, "side": decision, "signalAsOf": bars[index-1].date,
                "budget": str(budget) if decision == "BUY" else None}
            if strategy == "periodic":
                order["periodicCarryBefore"] = str(periodic_carry)
            count = len(portfolio.trades)
            if bar.volume == 0:
                order.update(status="not-filled", reason="zero-volume-ex-post-feasibility", filledQuantity=0)
            else:
                if decision == "BUY":
                    before = portfolio.quantity; portfolio.buy(bar.date, bar.open, budget)
                    has_bought = has_bought or portfolio.quantity > before
                else:
                    portfolio.sell(bar.date, bar.open)
                filled = len(portfolio.trades) > count
                if filled and strategy == "periodic":
                    # BUY cashDelta is -(gross including slippage + commission).
                    # Do not deduct reported slippage again from the reserve.
                    periodic_carry += int(portfolio.trades[-1]["cashDelta"])
                order.update(status="filled" if filled else "not-filled",
                    reason="modeled-open-execution" if filled else "cash-fees-position-or-t1-lot-constraint",
                    filledQuantity=portfolio.trades[-1]["quantity"] if filled else 0)
            if strategy == "periodic":
                order["periodicCarryAfter"] = str(periodic_carry)
            orders.append(order)
        last_decision = decision
        equity = portfolio.cash + _cents(bar.close * portfolio.quantity * 100)
        curve.append({"date": bar.date, "equity": str(equity), "cash": str(portfolio.cash),
            "quantity": portfolio.quantity, "externalFlow": str(flow),
            "openingEquityBeforeFlow": str(opening_equity_before_flow)})
    segments = {}
    for name, dates in partitions.items():
        if dates is None:
            segments[name] = {"from": None, "to": None, "metrics": None}; continue
        selected = [(i, row) for i, row in enumerate(curve) if dates["from"] <= row["date"] <= dates["to"]]
        subcurve = [row for _, row in selected]
        subtrades = [t for t in portfolio.trades if dates["from"] <= t["date"] <= dates["to"]]
        before = curve[selected[0][0]-1]["equity"] if selected and selected[0][0] else str(cfg["initialCash"])
        segments[name] = {**dates, "metrics": _metrics(subcurve, subtrades, cfg["initialCash"], before)}
    synthetic = quality["status"] == "synthetic"
    stance = "watch" if synthetic or last_decision is None else {"BUY": "accumulate", "SELL": "reduce", "HOLD": "hold"}[last_decision]
    signal_day = bars[indexes[-1]-1].date
    basis = _signal_basis(strategy, bars, indexes[-1], last_decision)
    return {"strategy": strategy, "equityCurve": curve, "trades": portfolio.trades, "orders": orders,
        "unusedPeriodicBudget": str(periodic_carry) if strategy == "periodic" else None,
        "metrics": _metrics(curve, portfolio.trades, cfg["initialCash"]), "partitions": segments,
        "skippedPublicationDates": skipped,
        "signal": {"stance": stance, "basis": basis,
            "risk": "仅历史价格路径研究；滑点、费用、公司行为及数据质量可能改变结果，不执行交易",
            "quality": "low" if synthetic or last_decision is None or not quality.get("corporateActionsVerified") or not quality.get("crossValidated") else "medium",
            "asOf": signal_day}}


def run_comparison(dataset, config=None):
    """Compare four fixed strategies on identical sessions and funding schedules.

    ``config.partitions`` accepts train/validation/test {from,to} date ranges.
    The fallback 60/20/20 boundaries are chosen by date before any strategy run.
    Dividends/splits are not invented. Prices must explicitly be unadjusted.
    """
    with localcontext() as context:
        context.prec = 200
        bars, quality = _prepare(dataset)
        cfg, indexes, partitions = _configuration(config, bars)
        limitations = [
            "历史研究，不是实时投资建议；不连接券商，不执行真实交易。",
            "MA20信号在前一完整日线收盘后形成，下一可用交易日开盘执行；v1为连续比较，不是交叉。",
            "原V1在14:50以当日分钟价执行；本研究仅复现其prevclose与MA20信号规则，四策略统一日线次日开盘假设，不复现原交易时点或原账户业绩。",
            "统一预留21条日线；MA20连续比较自身需20条，交叉需21条。所有策略使用共同区间和相同期初外部注资。",
            "收益为严格按开盘现金流分段的TWR：先算昨收至开盘注资前权益的隔夜收益，再算开盘注资后至收盘的日内收益，逐段链乘；新资金不分摊此前隔夜涨跌。不是相对累计投入本金的ROI或内部收益率。",
            "equityCurve.openingEquityBeforeFlow是开盘注资/下单前按开盘价计量的权益；externalFlow随后进入现金。分区沿用分区前收盘权益和同样的隔夜/日内链乘。回撤与波动率按每日收盘的分段TWR净值计算，不代表盘中最大回撤。",
            "定投首次从初始现金划出一期预算，不增加外部资金；未使用预算跨期累积，每个定投日尝试整手成交，实际含佣金买入支出扣预算，滑点已计入成交金额不重复扣。预算划分本身不改变现金或权益。",
            "默认100股整手、保守T+1；ETF也按T+1模拟，未建模个别品种T+0、涨跌停、停牌成交及流动性冲击。",
            "成交以开盘价加减固定滑点。指令仅由前序可用数据生成；当日完整成交量不参与信号，只在事后撮合回执中保守过滤整日零量无法成交。",
            "整日零量过滤属于事后成交可行性假设，开盘时无法观察当日完整成交量；它不证明真实开盘可成交，缺失交易日不填造价格。",
            "最大仓位仅在开盘新买入时检查，之后价格变化可能使市值占比越过阈值；不声称盘中动态风控。",
            "费用及整数分均按ROUND_HALF_UP；卖出股票收配置印花税，ETF免此税；费率是配置假设，非税务或券商收费声明。",
            "未复权价格回测不自动发放股息、不自动处理拆分；不能称为完整含息总回报。",
            "固定日期分区仅作样本内/外评估，未使用测试段调参；分区沿用完整模拟的仓位，不在边界重置本金。",
            "每股成交价以Decimal分表示，可能含分以下精度；现金、权益、费用均为整数分。",
            "年化按252交易日，短样本不具统计代表性；过度极端的年化数值返回null。",
        ]
        if any(bar.published is None for bar in bars):
            limitations.append("来源无发布时间的完整日线保守假定下一XSHG交易日开盘可用；该假设尚未独立验证。")
        if not quality.get("corporateActionsVerified"):
            limitations.append("公司行为未独立核验，价格变化可能含除权除息；不能由此得出完整真实收益结论。")
        if not quality.get("crossValidated"):
            limitations.append("未通过独立第二来源交叉核验；数据误差会影响结果。")
        if quality["status"] == "synthetic":
            limitations.insert(0, "全部为虚构合成数据，仅验证离线工程逻辑；收益和信号不得用作真实投资结论。")
        warnings = quality.get("warnings", [])
        if isinstance(warnings, list):
            limitations.extend(str(x) for x in warnings)
        strategies = {name: _run(name, bars, indexes, cfg, dataset, quality, partitions) for name in STRATEGIES}
        public_cfg = {key: str(cfg[key]) if key in {"initialCash", "minCommission", "periodicContribution"} else cfg[key] for key in DEFAULTS}
        return {"format": "goose-research-comparison", "version": 1, "symbol": dataset["symbol"],
            "kind": dataset["kind"], "currency": "CNY", "moneyUnit": "integer-cents", "executionPriceUnit": "decimal-cents-per-share",
            "dataStatus": quality["status"], "isRealPerformanceConclusion": False,
            "performanceUse": "synthetic-test-only" if quality["status"] == "synthetic" else "historical-price-path-research-only",
            "from": bars[indexes[0]].date, "to": bars[indexes[-1]].date,
            "datasetThrough": bars[-1].date, "valuationThrough": bars[indexes[-1]].date,
            "warmupBars": 21, "config": public_cfg, "partitions": partitions,
            "strategies": strategies, "limitations": limitations}
