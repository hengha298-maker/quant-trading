"""Fictional complete-session bars; no account files, network or live UI."""
import copy
from datetime import datetime
from decimal import Decimal, localcontext
import json
import unittest
from unittest.mock import patch

# Engine configures the reviewed V3.2/V3.3 local package paths.
from investment import engine
from investment.indicators import enrich_complete_session, previous_session_metrics, statistics
from investment.scenarios import build_scenarios
from goose_research.market import xshg_sessions


DAY = "2026-10-28"
INTRADAY = datetime.fromisoformat("2026-10-28T14:40:00+08:00")
POSTCLOSE = datetime.fromisoformat("2026-10-28T15:30:00+08:00")


def dataset():
    days = xshg_sessions("2026-08-01", DAY)[-35:]
    bars = []
    for i, day in enumerate(days):
        price = Decimal("20") + Decimal(i) / 100
        bars.append({"date": day, "open": str(price), "close": str(price),
                     "high": str(price), "low": str(price), "volume": str(100000 + i * 1000)})
    return {"symbol": "600900.SH", "adjustment": "none", "quality": {"status": "validated"}, "bars": bars}


def adjusted(ds):
    result = copy.deepcopy(ds)
    result.update(adjustment="qfq", quality={"status": "validated-basis-only", "ohlcAndCalendarValidated": True})
    for row in result["bars"]:
        for key in ("open", "high", "low", "close"):
            row[key] = str(Decimal(row[key]) / 2)
    return result


class IndicatorsTests(unittest.TestCase):
    def test_engine_directional_labels_cannot_bypass_scenario_gates(self):
        for reason in ("adjustment-conflict", "explicit-intraday-disallowed", "adjusted-context-missing"):
            with self.subTest(reason=reason):
                ds = dataset()
                qfq = adjusted(ds)
                if reason == "adjustment-conflict":
                    for i, row in enumerate(qfq["bars"]):
                        value = str(Decimal("30") - Decimal(i) / 100)
                        row.update(open=value, close=value, high=value, low=value)
                q = {"symbol": "600900.SH", "source": "fictional-source", "price": "20.34",
                     "sourceTimestamp": "2026-10-28T14:39:30+08:00", "quality": {
                         "status": "intraday-verified", "unitVerified": True,
                         "intradaySignalAllowed": reason != "explicit-intraday-disallowed"}}
                holdings = {"asOf": DAY, "availableCash": "1000.00", "missingBrokers": [], "positions": [
                    {"symbol": "600900.SH", "quantity": "100", "costPrice": "20.00", "holdingAsOf": DAY}]}
                original_metrics = previous_session_metrics(ds, DAY)
                original_metrics["corporateActionCaution"] = False  # exercise the future verified-action branch
                market = {"quotes": {"600900.SH": q}, "histories": {"600900.SH": ds},
                          "adjustedHistories": {} if reason == "adjusted-context-missing" else {"600900.SH": qfq}}
                with patch.object(engine, "metrics", side_effect=lambda d, _: copy.deepcopy(original_metrics) if d else None):
                    result = engine.build_analysis(market, holdings, now=INTRADAY)
                item = result["instruments"][0]
                self.assertEqual("watch", item["stance"])
                self.assertIn("观察", item["signal"])
                phone = engine.compatible_report(result)
                self.assertEqual("watch", phone["signals"][0]["stance"])

    def test_intraday_excludes_same_day_incomplete_bar_from_both_indicators(self):
        ds = dataset()
        expected = previous_session_metrics(ds, DAY)
        ds["bars"][-1].update(close="99999.99", volume="999999999999")
        actual = previous_session_metrics(ds, DAY)
        self.assertEqual(expected, actual)
        result = enrich_complete_session(actual, ds, adjusted(ds), INTRADAY)
        prior = ds["bars"][-2]
        self.assertEqual(prior["date"], result["through"])
        self.assertEqual(prior["date"], result["latestCompletedSession"]["through"])
        self.assertEqual(prior["close"], result["latestCompletedSession"]["close"])
        self.assertNotEqual("99999.99", result["latestCompletedSession"]["close"])

    def test_post_1530_includes_today_complete_bar_without_changing_v1(self):
        ds = dataset()
        prior = previous_session_metrics(ds, DAY)
        original = copy.deepcopy(prior)
        result = enrich_complete_session(prior, ds, adjusted(ds), POSTCLOSE)
        latest = result.pop("latestCompletedSession")
        self.assertEqual(original, result)
        self.assertEqual(original, prior, "enrichment must not mutate the archived prior-session rule")
        self.assertEqual(ds["bars"][-2]["date"], result["through"])
        self.assertEqual(DAY, latest["through"])
        self.assertEqual(ds["bars"][-1]["close"], latest["close"])
        self.assertEqual("complete-unadjusted-daily-price-only", latest["basis"])
        expected_ma = sum(Decimal(x["close"]) for x in ds["bars"][-20:]) / 20
        self.assertEqual(expected_ma, Decimal(latest["ma20"]))

    def test_1529_does_not_promote_the_today_bar(self):
        ds = dataset()
        prior = previous_session_metrics(ds, DAY)
        result = enrich_complete_session(prior, ds, adjusted(ds), datetime.fromisoformat("2026-10-28T15:29:59+08:00"))
        self.assertEqual(prior["through"], result["latestCompletedSession"]["through"])

    def test_postclose_without_today_bar_stays_on_prior_complete_date(self):
        ds = dataset()
        ds["bars"].pop()
        prior = previous_session_metrics(ds, DAY)
        result = enrich_complete_session(prior, ds, adjusted(ds), POSTCLOSE)
        self.assertEqual(prior["through"], result["latestCompletedSession"]["through"])
        self.assertNotEqual(DAY, result["latestCompletedSession"]["through"])

    def test_adjusted_series_is_separate_and_not_complete_total_return(self):
        ds = dataset()
        result = enrich_complete_session(previous_session_metrics(ds, DAY), ds, adjusted(ds), POSTCLOSE)
        latest = result["latestCompletedSession"]
        qfq = latest["qfqTrendDetails"]
        self.assertEqual(DAY, qfq["through"])
        self.assertEqual(Decimal(latest["close"]) / 2, Decimal(qfq["close"]))
        self.assertFalse(qfq["totalReturnVerified"])
        self.assertEqual("separate-provider-qfq-trend-not-total-return", qfq["basis"])
        self.assertEqual("unadjusted-price-only-dividend-events-not-accounted", result["returnBasis"])

    def test_adjusted_basis_identity_flags_and_all_dates_must_match(self):
        cases = [
            ("wrong symbol", lambda d: d.update(symbol="515450.SH")),
            ("unadjusted series mislabeled as qfq", lambda d: d.update(adjustment="none")),
            ("blocked quality", lambda d: d["quality"].update(status="blocked")),
            ("missing calendar flag", lambda d: d["quality"].pop("ohlcAndCalendarValidated")),
            ("early date missing outside latest20", lambda d: d["bars"].pop(0)),
            ("interior recent date missing", lambda d: d["bars"].pop(-10)),
            ("last date missing", lambda d: d["bars"].pop()),
            ("shifted historical date", lambda d: d["bars"][0].update(date="2026-08-03")),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                ds = dataset()
                qfq = adjusted(ds)
                mutate(qfq)
                result = enrich_complete_session(previous_session_metrics(ds, DAY), ds, qfq, POSTCLOSE)
                self.assertNotIn("qfqTrendDetails", result["latestCompletedSession"])

    def test_daily_gap_blocks_previous_rule_and_enrichment(self):
        ds = dataset()
        ds["bars"].pop(-6)
        self.assertIsNone(previous_session_metrics(ds, DAY))
        self.assertIsNone(enrich_complete_session(None, ds, adjusted(ds), POSTCLOSE))

    def test_blocked_daily_quality_cannot_generate_original_rule(self):
        ds = dataset()
        ds["quality"]["status"] = "blocked"
        self.assertIsNone(previous_session_metrics(ds, DAY))

    def test_2027_calendar_is_not_inferred(self):
        with self.assertRaisesRegex(ValueError, "CALENDAR"):
            previous_session_metrics(dataset(), "2027-01-04")

    def test_decimal_volatility_drawdown_volume_are_finite_and_reproducible(self):
        ds = dataset()
        a, b = statistics(ds["bars"]), statistics(copy.deepcopy(ds["bars"]))
        self.assertEqual(a, b)
        for key in ("maxPriceDrawdownPct", "annualizedPriceVolatilityPct", "volumeRatio"):
            self.assertTrue(Decimal(a[key]).is_finite())
        with localcontext() as ctx:
            ctx.prec = 60
            close = [Decimal(x["close"]) for x in ds["bars"]]
            returns = [close[i] / close[i-1] - 1 for i in range(1, len(close))]
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / (len(returns)-1)
            expected = format(variance.sqrt() * Decimal(252).sqrt() * 100, ".2f")
        self.assertEqual(expected, a["annualizedPriceVolatilityPct"])
        json.dumps(a, allow_nan=False)

    def test_constant_price_zero_volume_do_not_create_false_volatility_or_ratio(self):
        ds = dataset()
        for row in ds["bars"]:
            row.update(close="20.000", volume="0")
        result = statistics(ds["bars"])
        self.assertEqual("0.00", result["annualizedPriceVolatilityPct"])
        self.assertEqual("0.00", result["maxPriceDrawdownPct"])
        self.assertIsNone(result["volumeRatio"])

    def test_cached_intraday_scenarios_are_recomputed_as_blocked_when_quote_expires(self):
        q = {"symbol": "600900.SH", "price": "20.34", "sourceTimestamp": "2026-10-28T14:39:30+08:00",
             "quality": {"status": "intraday-verified", "unitVerified": True, "intradaySignalAllowed": True}}
        pf = {"status": "current-user-confirmed", "availableCash": "1000.00", "positions": [
            {"symbol": "600900.SH", "quantity": "100", "costPrice": "20.00", "weightPct": "30.00", "currentHoldingConfirmed": True}]}
        ds = dataset()
        m = enrich_complete_session(previous_session_metrics(ds, DAY), ds, adjusted(ds), INTRADAY)
        scenario = build_scenarios("600900.SH", q, m, pf, now=INTRADAY)
        report = {"generatedAt": INTRADAY.isoformat(), "asOf": DAY, "summary": "fictional research", "status": "portfolio-research",
                  "warnings": [], "portfolio": pf, "instruments": [{"symbol": "600900.SH", "quote": q, "metrics": m,
                  "stance": "watch", "signal": "fictional condition", "risks": [], "scenarioAnalysis": scenario}]}
        original = copy.deepcopy(report)
        result = engine.mark_report_freshness(report, now=datetime.fromisoformat("2026-10-28T14:43:00+08:00"))
        cached = result["instruments"][0]["scenarioAnalysis"]
        self.assertFalse(cached["evidenceQuality"]["currentIntradayVerified"])
        self.assertEqual("preferred", cached["scenarios"][0]["status"])
        self.assertTrue(all(s["status"] == "blocked" for s in cached["scenarios"][1:]))
        self.assertEqual(original, report)

    def test_next_day_cached_snapshot_does_not_remain_current_holdings(self):
        report = {"generatedAt": POSTCLOSE.isoformat(), "asOf": DAY, "summary": "fictional research", "status": "portfolio-research",
                  "warnings": [], "portfolio": {"status": "current-user-confirmed", "positions": [
                      {"symbol": "600900.SH", "quantity": "100", "currentHoldingConfirmed": True}]}, "instruments": []}
        result = engine.mark_report_freshness(report, now=datetime.fromisoformat("2026-10-29T14:40:00+08:00"))
        self.assertEqual("stale-snapshot", result["portfolio"]["status"])
        self.assertFalse(result["portfolio"]["positions"][0]["currentHoldingConfirmed"])
        self.assertFalse(result["portfolio"]["valuationIsCurrent"])


if __name__ == "__main__":
    unittest.main()
