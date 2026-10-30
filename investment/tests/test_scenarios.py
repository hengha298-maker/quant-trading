"""Entirely fictional holdings and prices; no personal files or live requests."""
import copy
from datetime import datetime
from decimal import Decimal
import json
import unittest

from investment.scenarios import build_scenarios


NOW = datetime.fromisoformat("2026-10-28T14:40:00+08:00")


def quote(symbol="600900.SH"):
    return {"symbol": symbol, "price": "24.30", "adjustment": "none",
            "source": "fictional-source", "sourceTimestamp": "2026-10-28T14:39:30+08:00",
            "quality": {"status": "intraday-verified", "unitVerified": True, "intradaySignalAllowed": True}}


def metrics():
    return {"through": "2026-10-27", "previousClose": "24.00", "ma20": "23.00",
            "v1Comparison": "above", "cross": "none", "maxPriceDrawdownPct": "-12.57",
            "annualizedPriceVolatilityPct": "17.30", "latestVsPrior19VolumeRatio": "1.23",
            "qfqContext": {"through": "2026-10-27", "previousClose": "24.00", "ma20": "23.00",
                           "comparison": "above", "basis": "provider-qfq-separate-not-total-return", "totalReturnVerified": False}}


def portfolio(symbol="600900.SH"):
    return {"status": "current-user-confirmed", "availableCash": "700.00",
            "weightBasis": "only-imported-securities-excludes-cash-and-other-assets",
            "positions": [{"symbol": symbol, "quantity": "200", "costPrice": "23.10",
                           "currentHoldingConfirmed": True, "weightPct": "35.25"}]}


def run(*, q=None, m=None, p=None, now=NOW):
    return build_scenarios("600900.SH", quote() if q is None else q, metrics() if m is None else m,
                           portfolio() if p is None else p, now=now)


class ScenariosTests(unittest.TestCase):
    def test_decimal_distance_and_percent_presentation(self):
        result = run()
        expected = ((Decimal("24") / Decimal("23") - 1) * 100).quantize(Decimal(".01"))
        self.assertEqual(str(expected), result["facts"]["completedCloseVsMa20Pct"])
        self.assertEqual("35.25", result["facts"]["importedSecuritiesWeightPct"])
        self.assertEqual("conditional-research", result["evidenceQuality"]["level"])
        self.assertIn("可继续持有观察", result["summary"])
        self.assertIn("不是均线交叉", result["modelConditions"]["originalV1Rule"])
        self.assertIn("没有新的交叉", result["modelConditions"]["independentCross"])

    def test_postclose_uses_latest_complete_day_but_does_not_rewrite_v1(self):
        q = quote()
        q.update(sourceTimestamp="2026-10-28T15:00:00+08:00")
        q["quality"].update(status="closing-snapshot", intradaySignalAllowed=False)
        m = metrics()
        m["latestCompletedSession"] = {"through": "2026-10-28", "close": "22.50", "ma20": "23.00",
                                       "volumeRatio": "1.70", "maxPriceDrawdownPct20d": "-7.30"}
        result = run(q=q, m=m, now=datetime.fromisoformat("2026-10-28T19:00:00+08:00"))
        self.assertEqual("2026-10-28", result["facts"]["trendThrough"])
        self.assertEqual("1.70", result["facts"]["completedDayVolumeRatio"])
        self.assertIn("2026-10-27", result["modelConditions"]["originalV1Rule"])
        self.assertIn("明显放量", result["summary"])
        self.assertIn("下个交易日研究条件", result["summary"])
        self.assertFalse(result["evidenceQuality"]["currentIntradayVerified"])

    def test_same_day_incomplete_bar_cannot_supply_trend(self):
        m = metrics()
        m["latestCompletedSession"] = {"through": "2026-10-28", "close": "24", "ma20": "23"}
        result = run(m=m)
        self.assertIsNone(result["facts"]["completedClose"])
        self.assertEqual("blocked", result["scenarios"][1]["status"])

    def test_cost_and_cash_unknown_never_imply_invented_budget(self):
        p = portfolio()
        p["availableCash"] = None
        p["positions"][0]["costPrice"] = None
        result = run(p=p)
        self.assertFalse(result["facts"]["availableCashKnown"])
        self.assertFalse(result["facts"]["costKnown"])
        self.assertEqual("blocked", result["scenarios"][2]["status"])
        self.assertTrue(all(s["quantity"] is None and s["budget"] is None for s in result["scenarios"]))

    def test_prices_and_holdings_are_not_mutated(self):
        q, m, p = quote(), metrics(), portfolio()
        before = copy.deepcopy([q, m, p])
        build_scenarios("600900.SH", q, m, p, now=NOW)
        self.assertEqual(before, [q, m, p])

    def test_concentration_aggregates_same_instrument_across_brokers(self):
        p = portfolio()
        p["positions"].append({"symbol": "600900.SH", "quantity": "300", "costPrice": "22.10",
                                "weightPct": "25.25", "currentHoldingConfirmed": True})
        result = run(p=p)
        self.assertEqual("60.50", result["facts"]["importedSecuritiesWeightPct"])
        self.assertEqual("500", result["facts"]["quantityFromConfirmedSnapshot"])
        self.assertEqual("condition-not-met", result["scenarios"][2]["status"])
        self.assertEqual("conditional-fit", result["scenarios"][3]["status"])
        self.assertIn("研究降风险", result["summary"])

    def test_concentration_prefers_exactly_aggregated_symbol_weight(self):
        p = portfolio()
        p["positions"] = [dict(p["positions"][0], weightPct=value) for value in ["33.34", "33.33", "33.34"]]
        self.assertEqual(Decimal("100.01"), sum(Decimal(row["weightPct"]) for row in p["positions"]))
        p["symbolWeights"] = [{"symbol": "600900.SH", "weightPct": "100.00"}]
        result = run(p=p)
        self.assertEqual("100.00", result["facts"]["importedSecuritiesWeightPct"])
        self.assertIn("100.00%", result["summary"])

    def test_missing_or_duplicate_canonical_weights_are_not_guessed_from_rows(self):
        for weights in ([], [{"symbol": "600900.SH", "weightPct": "10"}, {"symbol": "600900.SH", "weightPct": "20"}]):
            p = portfolio()
            p["symbolWeights"] = weights
            self.assertIsNone(run(p=p)["facts"]["importedSecuritiesWeightPct"])

    def test_same_date_adjustment_direction_disagreement_blocks_direction(self):
        m = metrics()
        m["qfqContext"].update(previousClose="21", ma20="22", comparison="below")
        result = run(m=m)
        self.assertTrue(result["modelConditions"]["adjustmentConflict"])
        self.assertIn("复权方向冲突", result["summary"])
        self.assertTrue(all(s["status"] == "blocked" for s in result["scenarios"][1:]))

    def test_different_date_adjusted_context_cannot_be_compared_as_same_day(self):
        m = metrics()
        m["qfqContext"].update(through="2026-10-26", previousClose="21", ma20="22", comparison="below")
        result = run(m=m)
        self.assertFalse(result["modelConditions"]["adjustmentConflict"])
        self.assertIsNone(result["modelConditions"]["adjustedSameDateComparison"])
        self.assertTrue(any("缺少同日期" in x for x in result["evidenceQuality"]["reasons"]))

    def test_quote_identity_unit_stale_and_future_are_never_live(self):
        for mutate in [lambda q: q.update(symbol="515450.SH"),
                       lambda q: q["quality"].update(unitVerified=False),
                       lambda q: q.update(sourceTimestamp="2026-10-28T14:30:00+08:00"),
                       lambda q: q.update(sourceTimestamp="2026-10-28T14:41:00+08:00"),
                       lambda q: q["quality"].update(status="blocked"),
                       lambda q: q.update(adjustment="qfq")]:
            q = quote()
            mutate(q)
            with self.subTest(q=q):
                result = run(q=q)
                self.assertFalse(result["evidenceQuality"]["currentIntradayVerified"])
                self.assertIn("不提供今天实时操作信号", result["summary"])
                self.assertEqual("preferred", result["scenarios"][0]["status"])
                self.assertTrue(all(s["status"] == "blocked" for s in result["scenarios"][1:]))

    def test_historical_quote_cannot_activate_current_holding_conditions(self):
        q = quote()
        q.update(sourceTimestamp="2026-10-27T15:00:00+08:00")
        q["quality"].update(status="historical-snapshot", intradaySignalAllowed=False)
        result = run(q=q)
        self.assertEqual("24.30", result["facts"]["price"], "the dated observation remains an honest fact")
        self.assertTrue(result["facts"]["holdingCurrent"])
        self.assertEqual("preferred", result["scenarios"][0]["status"])
        self.assertTrue(all(s["status"] == "blocked" for s in result["scenarios"][1:]))
        self.assertEqual("insufficient", result["evidenceQuality"]["level"])

    def test_missing_adjusted_context_never_promotes_directional_research(self):
        m = metrics()
        m.pop("qfqContext")
        result = run(m=m)
        self.assertTrue(all(s["status"] == "conditional-not-actionable" for s in result["scenarios"][1:]))
        self.assertTrue(all(any("先核验除权分红" in r for r in s["risks"]) for s in result["scenarios"][1:]))

    def test_stale_holdings_and_unknown_weight_stay_unknown(self):
        p = portfolio()
        p["status"] = "stale-snapshot"
        p["positions"][0].update(weightPct=None, currentHoldingConfirmed=False)
        result = run(p=p)
        self.assertIsNone(result["facts"]["importedSecuritiesWeightPct"])
        self.assertIsNone(result["facts"]["quantityFromConfirmedSnapshot"])
        self.assertFalse(result["facts"]["holdingCurrent"])
        self.assertIn("当前真实持仓未确认", result["summary"])

    def test_missing_everything_is_explicit_and_json_safe(self):
        result = run(q={}, m={}, p={})
        self.assertEqual("insufficient", result["evidenceQuality"]["level"])
        self.assertIsNone(result["facts"]["price"])
        self.assertIsNone(result["facts"]["completedDayVolumeRatio"])
        self.assertIsNone(result["facts"]["quantityFromConfirmedSnapshot"])
        json.dumps(result, allow_nan=False)

    def test_cross_is_independent_from_v1_and_differs_when_new_cross_exists(self):
        m = metrics()
        m["cross"] = "up"
        a = run(m=m)
        b = run()
        self.assertEqual(a["modelConditions"]["originalV1Rule"], b["modelConditions"]["originalV1Rule"])
        self.assertNotEqual(a["modelConditions"]["independentCross"], b["modelConditions"]["independentCross"])
        self.assertIn("历史向上交叉", a["summary"])

    def test_dividend_and_tplusone_constraints_are_visible(self):
        result = run()
        for scenario in result["scenarios"][2:]:
            risk = "；".join(scenario["risks"])
            self.assertIn("T+1", risk)
            self.assertIn("100股/份", risk)
            self.assertIn("不等于", risk)
            self.assertIn("分红", risk)
            self.assertIsNone(scenario["quantity"])
        self.assertTrue(result["modelConditions"]["priceOnlyNotTotalReturn"])
        self.assertFalse(result["modelConditions"]["automaticOrdersAllowed"])

    def test_low_trend_stock_and_etf_have_different_basis_not_generic_signals(self):
        q, m, p = quote("515450.SH"), metrics(), portfolio("515450.SH")
        m.update(previousClose="1.25", ma20="1.30", v1Comparison="below", cross="down", latestVsPrior19VolumeRatio="1.75")
        m["qfqContext"].update(previousClose="1.25", ma20="1.30", comparison="below")
        result = build_scenarios("515450.SH", q, m, p, now=NOW)
        self.assertIn("红利低波ETF", result["summary"])
        self.assertIn("-3.85", result["summary"])
        self.assertIn("明显放量", result["summary"])
        self.assertNotEqual(run()["summary"], result["summary"])
        self.assertEqual("conditional-fit", result["scenarios"][3]["status"])

    def test_nan_infinity_and_invalid_weights_are_not_numerical_facts(self):
        m, p = metrics(), portfolio()
        m.update(latestVsPrior19VolumeRatio="NaN", annualizedPriceVolatilityPct="Infinity")
        p["positions"][0]["weightPct"] = "100.01"
        result = run(m=m, p=p)
        self.assertIsNone(result["facts"]["completedDayVolumeRatio"])
        self.assertIsNone(result["facts"]["annualizedPriceVolatilityPct"])
        self.assertIsNone(result["facts"]["importedSecuritiesWeightPct"])
        json.dumps(result, allow_nan=False)

    def test_clock_must_have_explicit_timezone(self):
        with self.assertRaisesRegex(ValueError, "TIMEZONE"):
            run(now=datetime(2026, 10, 28, 14, 40))


if __name__ == "__main__":
    unittest.main()
