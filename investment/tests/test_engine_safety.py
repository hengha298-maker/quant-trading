"""Independent regression checks; entirely synthetic, with no network or accounts."""
import copy
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from investment import engine


NOW = datetime.fromisoformat("2026-10-28T14:44:00+08:00")


def quote(stamp="2026-10-28T14:43:30+08:00"):
    return {"source": "fictional-test-provider", "sourceTimestamp": stamp,
            "symbol": "600900.SH", "price": "25.12", "quality": {
                "status": "intraday-verified", "unitVerified": True,
                "intradaySignalAllowed": True}}


def report():
    return {"format": "goose-live-research", "version": 1,
            "generatedAt": NOW.isoformat(), "asOf": "2026-10-28",
            "status": "portfolio-research", "summary": "synthetic research",
            "warnings": [], "instruments": [{"symbol": "600900.SH",
            "quote": quote(), "stance": "hold", "signal": "synthetic hold", "risks": []}]}


def dataset():
    from goose_research.market import xshg_sessions
    days = xshg_sessions("2026-09-01", "2026-10-27")[-21:]
    bars = []
    for i, day in enumerate(days):
        price = format(Decimal("20") + Decimal(i) / 100, "f")
        bars.append({"date": day, "open": price, "high": price,
                     "low": price, "close": price, "volume": 100000 + i})
    return {"symbol": "600900.SH", "bars": bars,
            "quality": {"status": "validated"}}


def comparison():
    return {"from": "2026-09-01", "to": "2026-10-27", "strategies": {
        "buy_hold": {"metrics": {"totalReturnBps": 1, "maxDrawdownBps": -1,
                                   "tradeCount": 1, "cost": 500}}},
        "config": {"commissionBps": 3}, "limitations": ["synthetic price-only test"]}


def market_with_adjusted():
    ds = dataset()
    adjusted = {"symbol": "600900.SH", "adjustment": "qfq", "bars": copy.deepcopy(ds["bars"]),
                "quality": {"status": "validated-basis-only", "ohlcAndCalendarValidated": True,
                            "corporateActionsVerified": False}}
    return {"quotes": {"600900.SH": quote()}, "histories": {"600900.SH": ds},
            "adjustedHistories": {"600900.SH": adjusted}}


class EngineSafetyRegressionTests(unittest.TestCase):
    def test_prior_day_report_is_historical_watch_not_current(self):
        old = report()
        result = engine.mark_report_freshness(old, now=datetime.fromisoformat("2026-10-29T14:40:00+08:00"))
        self.assertEqual("stale-report", result["status"])
        self.assertTrue(result["cachedReport"])
        self.assertIn("历史报告", result["summary"])
        self.assertEqual("watch", result["instruments"][0]["stance"])
        self.assertEqual("historical-snapshot", result["instruments"][0]["quote"]["quality"]["status"])
        self.assertEqual("portfolio-research", old["status"], "viewing must not overwrite archived report")

    def test_today_generated_report_cannot_hide_old_market_asof(self):
        old = report()
        old["asOf"] = "2025-01-02"
        result = engine.mark_report_freshness(old, now=NOW)
        self.assertEqual("stale-report", result["status"])
        self.assertEqual("watch", result["instruments"][0]["stance"])

    def test_cached_intraday_quote_over_90_seconds_is_not_current(self):
        old = report()
        old["instruments"][0]["quote"] = quote("2026-10-28T14:40:00+08:00")
        result = engine.mark_report_freshness(old, now=NOW)
        item = result["instruments"][0]
        self.assertEqual("watch", item["stance"])
        self.assertEqual("historical-snapshot", item["quote"]["quality"]["status"])
        self.assertFalse(item["quote"]["quality"]["intradaySignalAllowed"])
        self.assertIn("90秒", item["risks"][0])

    def test_future_cached_quote_is_not_current(self):
        old = report()
        old["instruments"][0]["quote"] = quote("2026-10-28T14:45:00+08:00")
        item = engine.mark_report_freshness(old, now=NOW)["instruments"][0]
        self.assertEqual("watch", item["stance"])
        self.assertFalse(item["quote"]["quality"]["intradaySignalAllowed"])

    def test_missing_holdings_never_recreates_historical_quantities(self):
        result = engine.build_analysis({"quotes": {"600900.SH": quote()}, "histories": {}}, None, now=NOW)
        self.assertEqual("holdings-required", result["status"])
        self.assertEqual([], result["portfolio"]["positions"])
        self.assertIsNone(result["portfolio"]["availableCash"])
        self.assertIsNone(result["portfolio"]["marketValue"])
        self.assertTrue(all(i["stance"] == "watch" for i in result["instruments"]))
        self.assertTrue(all("quantity" not in i for i in result["instruments"]))

    def test_raw_ma_excludes_same_day_unfinished_bar(self):
        ds = dataset()
        expected = engine.metrics(ds, "2026-10-28")
        ds["bars"].append({"date": "2026-10-28", "close": "999999", "volume": 999999})
        actual = engine.metrics(ds, "2026-10-28")
        self.assertEqual(expected, actual)
        self.assertEqual("2026-10-27", actual["through"])
        self.assertTrue(actual["corporateActionCaution"])
        self.assertIn("dividend-events-not-accounted", actual["returnBasis"])

    def test_validated_matching_qfq_has_separate_not_total_return_context(self):
        market = market_with_adjusted()
        with patch("goose_research.backtest.run_comparison", return_value=comparison()):
            result = engine.build_analysis(market, None, now=NOW)
        stock = result["instruments"][0]
        self.assertIn("qfqContext", stock["metrics"])
        qfq = stock["metrics"]["qfqContext"]
        self.assertFalse(qfq["totalReturnVerified"])
        self.assertEqual("provider-qfq-separate-not-total-return", qfq["basis"])
        self.assertFalse(stock["backtest"]["completeTotalReturn"])
        self.assertEqual("price-only-not-total-return", stock["backtest"]["basis"])
        self.assertEqual("watch", stock["stance"])
        self.assertTrue(any("不是完整含息总回报" in r for r in stock["risks"]))

    def test_qfq_requires_quality_symbol_flag_and_20_matching_dates(self):
        cases = [
            ("wrong symbol", lambda x: x.update(symbol="515450.SH")),
            ("wrong adjustment", lambda x: x.update(adjustment="none")),
            ("blocked quality", lambda x: x["quality"].update(status="blocked")),
            ("calendar flag absent", lambda x: x["quality"].pop("ohlcAndCalendarValidated")),
            ("fewer than 20 days", lambda x: x.update(bars=x["bars"][-19:])),
            ("last date missing", lambda x: x["bars"].pop()),
            ("interior missing date", lambda x: x["bars"].pop(7)),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                market = market_with_adjusted()
                mutate(market["adjustedHistories"]["600900.SH"])
                with patch("goose_research.backtest.run_comparison", return_value=comparison()):
                    result = engine.build_analysis(market, None, now=NOW)
                self.assertNotIn("qfqContext", result["instruments"][0]["metrics"])

    def test_analysis_side_effect_flags_are_false(self):
        result = engine.build_analysis({"quotes": {}, "histories": {}}, None, now=NOW)
        self.assertFalse(result["notificationSent"])
        self.assertFalse(result["scheduleEnabled"])
        self.assertFalse(result["realOrdersEnabled"])
        phone = engine.compatible_report(result)
        self.assertEqual([], phone["paperTrades"])
        self.assertIsNone(phone["backtest"])


if __name__ == "__main__":
    unittest.main()
