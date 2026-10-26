"""Offline, fictitious daily bars; never imports or runs the V1 application."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research.backtest import run_comparison, _prepare, _configuration, _Portfolio, _metrics


def fixture(prices=None, kind="stock", status="synthetic"):
    prices = prices or [str(10 + i / 10) for i in range(65)]
    days = []; day = date(2025, 1, 2)
    while len(days) < len(prices):
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    bars = [{"date": day, "open": str(price), "high": str(Decimal(str(price))+1),
        "low": str(max(Decimal("0.001"), Decimal(str(price))-1)), "close": str(price),
        "volume": 1000000, "publishedAt": None} for day, price in zip(days, prices)]
    return {"symbol": "600900.SH" if kind == "stock" else "515450.SH", "kind": kind,
        "adjustment": "none", "currency": "CNY", "priceUnit": "CNY-per-share", "bars": bars,
        "calendar": {"name": "XSHG", "sessions": days},
        "quality": {"status": status, "unitVerified": True, "corporateActionsVerified": False,
            "crossValidated": False, "warnings": []}}


def config(**extra):
    return {"commissionBps": 0, "slippageBps": 0, "stampDutyBps": 0,
        "minCommission": "0", "periodicContribution": "0", **extra}


def portfolio(kind="stock", **extra):
    bars, _ = _prepare(fixture(kind=kind))
    cfg, _, _ = _configuration(config(**extra), bars)
    return _Portfolio(cfg, kind, "600900.SH" if kind == "stock" else "515450.SH")


class BacktestTests(unittest.TestCase):
    def test_v1_continuously_buys_above_ma20_but_cross_does_not_invent_a_cross(self):
        result = run_comparison(fixture(), config(buyBudgetBps=1000))
        v1 = result["strategies"]["v1_ma20"]["trades"]
        cross = result["strategies"]["ma20_cross"]["trades"]
        self.assertGreater(len(v1), 1)
        self.assertTrue(all(t["side"] == "BUY" for t in v1))
        self.assertEqual(cross, [])
        self.assertTrue(all(t["quantity"] % 100 == 0 for t in v1))

    def test_ma20_cross_is_a_separate_rule_and_waits_for_an_observed_cross(self):
        prices = [str(Decimal(30)-Decimal(i)/2) for i in range(30)] + [str(Decimal(15)+i) for i in range(35)]
        result = run_comparison(fixture(prices), config())
        trades = result["strategies"]["ma20_cross"]["trades"]
        self.assertTrue(trades)
        self.assertEqual(trades[0]["side"], "BUY")
        self.assertGreater(trades[0]["date"], result["from"])

    def test_current_day_close_never_changes_its_opening_order(self):
        original = fixture(); changed = deepcopy(original); index = 25
        changed["bars"][index]["close"] = "200"
        changed["bars"][index]["high"] = "201"
        a = run_comparison(original, config(buyBudgetBps=2000))
        b = run_comparison(changed, config(buyBudgetBps=2000))
        day = original["bars"][index]["date"]
        for name in a["strategies"]:
            first = [x for x in a["strategies"][name]["trades"] if x["date"] <= day]
            second = [x for x in b["strategies"][name]["trades"] if x["date"] <= day]
            self.assertEqual(first, second, name)

    def test_late_publication_skips_trading_before_open_and_null_assumption_is_reported(self):
        data = fixture()
        first_day = data["bars"][21]["date"]
        data["bars"][20]["publishedAt"] = first_day + "T10:00:00+08:00"
        result = run_comparison(data, config())
        for item in result["strategies"].values():
            self.assertIn(first_day, item["skippedPublicationDates"])
            self.assertFalse(any(t["date"] == first_day for t in item["trades"]))
        self.assertTrue(any("发布时间" in text and "下一" in text for text in result["limitations"]))

    def test_t_plus_one_and_board_lot_are_enforced_for_stock_and_etf(self):
        for kind in ("stock", "etf"):
            p = portfolio(kind, initialCash="20501")
            p.buy("2025-01-02", Decimal("1"), 20501)
            self.assertEqual(p.quantity, 200)
            p.sell("2025-01-02", Decimal("1"))
            self.assertEqual(len(p.trades), 1)
            p.sell("2025-01-03", Decimal("1"))
            self.assertEqual(p.quantity, 0)
            self.assertEqual(p.cash, 20501)

    def test_commission_minimum_tax_sell_only_and_etf_exemption(self):
        for kind, expected in (("stock", "10"), ("etf", "0")):
            p = portfolio(kind, initialCash="100000", commissionBps=1, minCommission="500", stampDutyBps=10)
            p.buy("2025-01-02", Decimal("1"), 10500)
            p.sell("2025-01-03", Decimal("1"))
            self.assertEqual(p.trades[0]["commission"], "500")
            self.assertEqual(p.trades[0]["stampDuty"], "0")
            self.assertEqual(p.trades[1]["stampDuty"], expected)
            self.assertEqual(p.cash, 100000-1000-int(expected))

    def test_slippage_is_reported_once_and_etf_fractional_cent_ticks_are_preserved(self):
        p = portfolio("etf", initialCash="100000", slippageBps=10)
        p.buy("2025-01-02", Decimal("1.234"), 13000)
        trade = p.trades[0]
        self.assertEqual(Decimal(trade["price"]), Decimal("123.5234"))
        self.assertEqual(trade["slippage"], "12")
        self.assertEqual(trade["totalCost"], "12")
        self.assertEqual(p.cash, 100000-int(trade["gross"]))

    def test_position_limit_includes_execution_fees_at_open(self):
        p = portfolio(initialCash="100000", maxPositionBps=2000, minCommission="500")
        p.buy("2025-01-02", Decimal("1"), 100000)
        marked = p.quantity * 100
        self.assertLessEqual(marked*10000, (p.cash+marked)*2000)
        self.assertEqual(p.quantity, 100)

    def test_same_injections_and_cash_flow_adjusted_returns_for_all_four_strategies(self):
        data = fixture(["10"]*65)
        result = run_comparison(data, config(periodicEvery=5, periodicContribution="100000"))
        curves = [s["equityCurve"] for s in result["strategies"].values()]
        flows = [[r["externalFlow"] for r in c] for c in curves]
        self.assertTrue(any(int(x) for x in flows[0]))
        self.assertTrue(all(f == flows[0] for f in flows))
        for item in result["strategies"].values():
            self.assertEqual(item["metrics"]["totalReturnBps"], 0)
            self.assertEqual(item["metrics"]["maxDrawdownBps"], 0)
            self.assertEqual(item["metrics"]["volatilityBps"], 0)
            self.assertEqual(int(item["equityCurve"][-1]["equity"]), 10000000+sum(map(int,flows[0])))

    def test_integer_precision_exceeds_binary_float_range(self):
        data = fixture(["1.234"]*45, kind="etf")
        result = run_comparison(data, config(initialCash="900719925474099301", periodicEvery=2, periodicContribution="101"))
        for item in result["strategies"].values():
            self.assertTrue(all(isinstance(row["equity"], str) and row["equity"].isdigit() for row in item["equityCurve"]))
            with localcontext() as ctx:
                ctx.prec = 100
                for row in item["equityCurve"]:
                    mark = int((Decimal("1.234")*row["quantity"]*100).quantize(Decimal(1)))
                    self.assertEqual(int(row["equity"]), int(row["cash"])+mark)
            self.assertEqual(item["metrics"]["totalReturnBps"], 0)

    def test_fixed_date_partitions_share_full_simulation_without_parameter_search(self):
        data = fixture(); days = data["calendar"]["sessions"]
        partitions = {"train":{"from":days[21],"to":days[35]},"validation":{"from":days[36],"to":days[48]},"test":{"from":days[49],"to":days[-1]}}
        result = run_comparison(data, config(partitions=partitions))
        self.assertEqual(result["from"], days[21]); self.assertEqual(result["to"], days[-1])
        self.assertEqual(result["partitions"], partitions)
        for item in result["strategies"].values():
            self.assertEqual(item["equityCurve"][0]["date"], days[21])
            self.assertEqual(set(item["partitions"]), {"train", "validation", "test"})
            self.assertEqual(item["partitions"]["test"]["metrics"]["tradingDays"], len(days)-49)
        with self.assertRaises(ValueError):
            run_comparison(data, config(partitions=partitions, start=days[30]))

    def test_blocked_unverified_etf_missing_sessions_and_adjustment_are_rejected(self):
        cases = []
        blocked = fixture(); blocked["quality"]["status"] = "blocked"; cases.append(blocked)
        etf = fixture(kind="etf"); etf["quality"]["unitVerified"] = False; cases.append(etf)
        missing = fixture(); missing["bars"].pop(25); cases.append(missing)
        adjusted = fixture(); adjusted["adjustment"] = "qfq"; cases.append(adjusted)
        duplicate = fixture(); duplicate["bars"][22] = deepcopy(duplicate["bars"][21]); cases.append(duplicate)
        early = fixture(); early["bars"][21]["publishedAt"] = early["bars"][21]["date"]+"T09:00:00+08:00"; cases.append(early)
        for item in cases:
            with self.subTest(item=item["quality"]["status"]), self.assertRaises(ValueError):
                run_comparison(item, config())

    def test_synthetic_signals_are_watch_only_and_inputs_are_never_changed(self):
        data = fixture(); before = deepcopy(data)
        result = run_comparison(data, config())
        self.assertEqual(data, before)
        self.assertFalse(result["isRealPerformanceConclusion"])
        self.assertEqual(result["performanceUse"], "synthetic-test-only")
        self.assertTrue(any("虚构" in x for x in result["limitations"]))
        self.assertTrue(any("14:50" in x for x in result["limitations"]))
        for item in result["strategies"].values():
            self.assertEqual(item["signal"]["stance"], "watch")
            self.assertEqual(item["signal"]["quality"], "low")

    def test_zero_volume_session_never_creates_an_execution(self):
        data = fixture()
        day = data["bars"][21]["date"]
        data["bars"][21]["volume"] = 0
        result = run_comparison(data, config())
        for item in result["strategies"].values():
            self.assertFalse(any(t["date"] == day for t in item["trades"]))

    def test_unavailable_final_signal_is_watch_not_a_claim_of_published_data(self):
        data = fixture(status="validated")
        last_day = data["bars"][-1]["date"]
        data["bars"][-2]["publishedAt"] = last_day+"T10:00:00+08:00"
        result = run_comparison(data, config())
        for item in result["strategies"].values():
            self.assertEqual(item["signal"]["stance"], "watch")
            self.assertIn("已跳过", item["signal"]["basis"])

    def test_equal_ma20_is_hold_and_shared_warmup_uses_no_first_twenty_one_bars(self):
        data = fixture(["10"]*65)
        result = run_comparison(data, config())
        self.assertEqual(result["from"], data["bars"][21]["date"])
        self.assertEqual(result["strategies"]["v1_ma20"]["trades"], [])
        self.assertEqual(result["strategies"]["ma20_cross"]["trades"], [])

    def test_nonsensical_or_float_money_configuration_is_rejected(self):
        for options in ({"initialCash": 100.0}, {"minCommission": "-1"}, {"buyBudgetBps": 10001}, {"periodicEvery": 0}, {"slippageBps": 10000}, {"commissionBps": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                run_comparison(fixture(), config(**options))

    def test_signal_basis_exposes_exact_prior_close_average_window_and_historical_dates(self):
        data = fixture(status="validated")
        result = run_comparison(data, config())
        signal = result["strategies"]["v1_ma20"]["signal"]
        window = data["bars"][-21:-1]
        expected = sum(Decimal(bar["close"]) for bar in window)/20
        self.assertIn("前收盘"+window[-1]["close"]+"元", signal["basis"])
        self.assertIn("MA20 "+format(expected, "f")+"元", signal["basis"])
        self.assertIn(window[0]["date"]+"至"+window[-1]["date"], signal["basis"])
        self.assertIn("连续比较结果BUY", signal["basis"])
        self.assertIn("不是今日或实时信号", signal["basis"])
        self.assertEqual(signal["asOf"], window[-1]["date"])
        self.assertEqual(result["datasetThrough"], data["bars"][-1]["date"])
        self.assertNotEqual(signal["asOf"], result["datasetThrough"])
        self.assertIn("独立交叉结果", result["strategies"]["ma20_cross"]["signal"]["basis"])

    def test_current_volume_changes_only_fill_receipt_not_signal_or_generated_instruction(self):
        data = fixture(status="validated"); zero = deepcopy(data)
        zero["bars"][-1]["volume"] = 0
        day = data["bars"][-1]["date"]
        options = config(start=day, end=day)
        a, b = run_comparison(data, options), run_comparison(zero, options)
        for name in a["strategies"]:
            first, second = a["strategies"][name], b["strategies"][name]
            self.assertEqual(first["signal"], second["signal"])
            intent = lambda order: {key:order[key] for key in ("date","side","signalAsOf","budget")}
            self.assertEqual(list(map(intent,first["orders"])), list(map(intent,second["orders"])))
        self.assertTrue(a["strategies"]["v1_ma20"]["trades"])
        self.assertEqual(b["strategies"]["v1_ma20"]["trades"], [])
        receipt = b["strategies"]["v1_ma20"]["orders"][0]
        self.assertEqual(receipt["reason"], "zero-volume-ex-post-feasibility")
        self.assertEqual(receipt["status"], "not-filled")
        self.assertTrue(any("事后" in item and "开盘时无法观察" in item for item in b["limitations"]))

    def test_changing_a_future_close_cannot_change_todays_generated_orders(self):
        data = fixture(); future = deepcopy(data)
        future["bars"][30]["close"] = "200"
        future["bars"][30]["high"] = "201"
        a, b = run_comparison(data, config(buyBudgetBps=2000)), run_comparison(future, config(buyBudgetBps=2000))
        today = data["bars"][25]["date"]
        for name in a["strategies"]:
            self.assertEqual([o for o in a["strategies"][name]["orders"] if o["date"] <= today],
                [o for o in b["strategies"][name]["orders"] if o["date"] <= today])

    def test_buy_budget_and_position_cap_include_commission_and_embedded_slippage(self):
        p = portfolio(initialCash="100000", maxPositionBps=5000, minCommission="500", slippageBps=100)
        p.buy("2025-01-02", Decimal("1"), 100000)
        self.assertEqual(p.quantity,400)
        trade = p.trades[0]
        self.assertEqual(trade["gross"],"40400")
        self.assertEqual(trade["commission"],"500")
        self.assertEqual(trade["slippage"],"400")
        self.assertEqual(p.cash,59100)
        self.assertLessEqual(p.quantity*100*10000,(p.cash+p.quantity*100)*5000)
        limited = portfolio(initialCash="100000", minCommission="500", slippageBps=100)
        limited.buy("2025-01-02", Decimal("1"), 40000)
        self.assertEqual(limited.quantity,300)
        self.assertLessEqual(-int(limited.trades[0]["cashDelta"]),40000)

    def test_periodic_unspent_allowances_accumulate_until_one_hundred_shares_are_affordable(self):
        data = fixture(["30"]*45)
        result = run_comparison(data, config(periodicEvery=5,periodicContribution="100000"))
        periodic = result["strategies"]["periodic"]
        orders = periodic["orders"]
        self.assertEqual(orders[0]["periodicCarryBefore"],"100000")
        self.assertEqual(orders[0]["status"],"not-filled")
        self.assertEqual(orders[1]["periodicCarryBefore"],"200000")
        self.assertEqual(orders[1]["status"],"not-filled")
        self.assertEqual(orders[2]["periodicCarryBefore"],"300000")
        self.assertEqual(orders[2]["status"],"filled")
        self.assertEqual(orders[2]["filledQuantity"],100)
        self.assertEqual(orders[2]["periodicCarryAfter"],"0")
        self.assertEqual(periodic["trades"][0]["quantity"],100)
        self.assertEqual(periodic["trades"][0]["gross"],"300000")
        self.assertEqual(periodic["equityCurve"][0]["externalFlow"],"0")
        self.assertEqual(periodic["equityCurve"][0]["equity"],"10000000")

    def test_periodic_carry_deducts_actual_fees_once_and_preserves_common_funding_and_caps(self):
        data = fixture(["30"]*65)
        result = run_comparison(data,config(initialCash="1000000",periodicEvery=2,periodicContribution="100000",maxPositionBps=4000,minCommission="500",slippageBps=10))
        periodic = result["strategies"]["periodic"]
        curves = [item["equityCurve"] for item in result["strategies"].values()]
        common_flows = [r["externalFlow"] for r in curves[0]]
        self.assertTrue(all([r["externalFlow"] for r in curve]==common_flows for curve in curves))
        spent = sum(-int(trade["cashDelta"]) for trade in periodic["trades"])
        allowance = len(periodic["orders"])*100000
        self.assertEqual(int(periodic["unusedPeriodicBudget"]),allowance-spent)
        self.assertTrue(periodic["trades"])
        for trade in periodic["trades"]:
            self.assertEqual(-int(trade["cashDelta"]),int(trade["gross"])+int(trade["commission"]))
        for order in periodic["orders"]:
            self.assertGreaterEqual(int(order["periodicCarryAfter"]),0)
            if order["status"]=="filled":
                trade = next(t for t in periodic["trades"] if t["date"]==order["date"])
                self.assertEqual(int(order["periodicCarryBefore"])-int(order["periodicCarryAfter"]),-int(trade["cashDelta"]))
        for row in periodic["equityCurve"]:
            self.assertGreaterEqual(int(row["cash"]),0)
            self.assertLessEqual(row["quantity"]*3000*10000,int(row["equity"])*4000)

    def test_opening_contribution_cannot_dilute_an_overnight_one_hundred_percent_gain(self):
        prices=["10"]*65
        prices[22:] = ["20"]*(65-22)
        data=fixture(prices)
        days=data["calendar"]["sessions"]
        result=run_comparison(data,config(initialCash="100000",start=days[21],end=days[22],periodicEvery=1,periodicContribution="100000"))
        buy_hold=result["strategies"]["buy_hold"]
        first,second=buy_hold["equityCurve"]
        self.assertEqual(first["equity"],"100000")
        self.assertEqual(second["openingEquityBeforeFlow"],"200000")
        self.assertEqual(second["externalFlow"],"100000")
        self.assertEqual(second["equity"],"300000")
        self.assertEqual(buy_hold["metrics"]["totalReturnBps"],10000)
        self.assertEqual(buy_hold["partitions"]["validation"]["metrics"]["totalReturnBps"],10000)
        self.assertEqual(buy_hold["metrics"]["returnMethod"],"subperiod-TWR-overnight-then-intraday-opening-flows")

    def test_exact_twr_without_external_flows_telescopes_to_end_equity_over_start(self):
        data=fixture(["10"]*22+["20"]*43)
        days=data["calendar"]["sessions"]
        result=run_comparison(data,config(initialCash="100000",start=days[21],end=days[22]))
        item=result["strategies"]["buy_hold"]
        self.assertEqual(item["equityCurve"][-1]["equity"],"200000")
        self.assertEqual(item["metrics"]["totalReturnBps"],10000)
        self.assertTrue(all(row["externalFlow"]=="0" for row in item["equityCurve"]))

    def test_intraday_fees_reduce_exact_flow_twr_after_the_overnight_subperiod(self):
        row={"date":"2025-01-03","equity":"299500","cash":"99500","quantity":100,
            "externalFlow":"100000","openingEquityBeforeFlow":"200000"}
        metrics=_metrics([row],[{"totalCost":"500"}],100000)
        # Existing capital doubles overnight. A 500-cent cost then reduces the
        # combined 300000-cent funded opening balance: 2*(299500/300000)-1.
        self.assertEqual(metrics["totalReturnBps"],9967)
        self.assertEqual(metrics["cost"],"500")
        self.assertEqual(metrics["maxDrawdownBps"],0)
        self.assertEqual(_metrics([row],[],100000,before="100000")["totalReturnBps"],9967)

    def test_missing_opening_equity_cannot_silently_fall_back_to_approximate_flow_returns(self):
        with self.assertRaisesRegex(ValueError,"CURVE_OPENING_EQUITY_REQUIRED_FOR_EXACT_TWR"):
            _metrics([{"date":"2025-01-03","equity":"300000","externalFlow":"100000"}],[],100000)


if __name__ == "__main__":
    unittest.main()
