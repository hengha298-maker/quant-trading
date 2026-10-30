"""Fictional brokerage inputs only; no saved holdings, network or file writes."""
import copy
import unittest

from investment.accounting import build_portfolio


DAY = "2026-10-28"


def quote(symbol="600001.SH", price="12.34", **changes):
    value = {"symbol": symbol, "price": price, "source": "fictional-provider",
             "sourceTimestamp": DAY + "T15:00:00+08:00", "currency": "CNY",
             "priceUnit": "CNY-per-share", "adjustment": "none",
             "quality": {"status": "closing-snapshot", "unitVerified": True}}
    value.update(changes)
    return value


def position(symbol="600001.SH", quantity=100, cost="10.00", **changes):
    value = {"symbol": symbol, "name": "虚构标的", "quantity": quantity,
             "costPrice": cost, "broker": "虚构券商甲", "holdingAsOf": DAY,
             "reportedMarketValue": "1200.00", "reportedPnl": "190.01"}
    value.update(changes)
    return value


def holdings(positions=None, accounts=None, **changes):
    value = {"asOf": DAY, "positions": positions if positions is not None else [position()],
             "availableCash": "0", "missingBrokers": [], "accounts": accounts if accounts is not None else [
                 {"asOf": DAY, "availableCash": "0", "source": {"broker": "虚构券商甲", "kind": "csv"}}]}
    value.update(changes)
    return value


class AccountingTests(unittest.TestCase):
    def test_reported_broker_values_never_replaced_by_revaluation(self):
        source = holdings()
        before = copy.deepcopy(source)
        report = build_portfolio(source, {"600001.SH": quote()}, DAY)
        row = report["positions"][0]
        self.assertEqual("190.01", row["reportedPnl"])
        self.assertEqual("1200.00", row["reportedMarketValue"])
        self.assertEqual("1234.00", row["marketValue"])
        self.assertEqual("234.00", row["computedPnl"])
        self.assertEqual("43.99", row["pnlDifference"])
        self.assertEqual("190.01", report["reportedPnl"])
        self.assertEqual("43.99", report["pnlDifference"])
        self.assertIn("尚未核验", row["pnlDifferenceBasis"])
        self.assertIn("未另扣", row["feeBasis"])
        self.assertEqual(before, source, "analysis must not mutate an imported snapshot")

    def test_reported_zero_and_negative_profit_are_real_values(self):
        for raw in ("0", "-1.13"):
            with self.subTest(raw=raw):
                result = build_portfolio(holdings([position(reportedPnl=raw)]), {"600001.SH": quote()}, DAY)
                self.assertEqual(raw, result["positions"][0]["reportedPnl"])
                self.assertIsNotNone(result["reportedPnl"])

    def test_unknown_imported_pnl_and_cost_are_not_zero(self):
        report = build_portfolio(holdings([position(cost=None, reportedPnl=None)]), {"600001.SH": quote()}, DAY)
        self.assertEqual("1234.00", report["marketValue"])
        self.assertIsNone(report["costValue"])
        self.assertIsNone(report["pnl"])
        self.assertIsNone(report["computedPnl"])
        self.assertIsNone(report["reportedPnl"])
        self.assertIsNone(report["pnlDifference"])

    def test_unknown_cash_is_not_zero_and_known_zero_is_preserved(self):
        known = build_portfolio(holdings(), {"600001.SH": quote()}, DAY)
        self.assertEqual("0.00", known["availableCash"])
        unknown = holdings()
        unknown["accounts"][0]["availableCash"] = None
        # A misleading aggregate cannot override its unknown account source.
        unknown["availableCash"] = "12345"
        self.assertIsNone(build_portfolio(unknown, {"600001.SH": quote()}, DAY)["availableCash"])

    def test_two_securities_do_not_duplicate_one_accounts_cash_or_market_value(self):
        rows = [position(reportedMarketValue="999999"), position("600002.SH", reportedMarketValue="999999")]
        source = holdings(rows)
        source["accounts"][0]["availableCash"] = "7.89"
        result = build_portfolio(source, {s: quote(s) for s in ("600001.SH", "600002.SH")}, DAY)
        self.assertEqual("7.89", result["availableCash"])
        self.assertEqual("2468.00", result["marketValue"])
        self.assertEqual("1999998.00", result["reportedMarketValue"])
        self.assertEqual("2468.00", result["accounts"][0]["marketValue"])

    def test_partial_quotes_keep_portfolio_totals_weights_and_concentration_unknown(self):
        rows = [position(), position("600002.SH")]
        result = build_portfolio(holdings(rows), {"600001.SH": quote()}, DAY)
        self.assertIsNone(result["marketValue"])
        self.assertIsNone(result["pnl"])
        self.assertIsNone(result["concentration"])
        self.assertEqual("1234.00", result["knownMarketValueSubtotal"])
        self.assertEqual(1, result["knownMarketValueCount"])
        self.assertTrue(all(p["weightPct"] is None for p in result["positions"]))
        self.assertEqual([], result["symbolWeights"])
        self.assertEqual("380.02", result["reportedPnl"], "original export values survive unavailable live quotes")

    def test_same_security_across_brokers_is_combined_for_concentration(self):
        rows = [position(quantity=100), position(quantity=100, broker="虚构券商乙"), position("600002.SH", quantity=100)]
        accounts = [
            {"asOf": DAY, "availableCash": "0", "source": {"broker": "虚构券商甲", "kind": "csv"}},
            {"asOf": DAY, "availableCash": "0", "source": {"broker": "虚构券商乙", "kind": "manual"}},
        ]
        result = build_portfolio(holdings(rows, accounts), {s: quote(s, price="1.00") for s in ("600001.SH", "600002.SH")}, DAY)
        self.assertEqual("66.67", result["concentration"])
        self.assertEqual("33.33", result["positions"][0]["weightPct"])
        self.assertEqual("66.67", result["positions"][0]["symbolWeightPct"])
        self.assertEqual(2, len(result["symbolWeights"]))
        self.assertEqual("200.00", result["accounts"][0]["marketValue"])
        self.assertEqual("100.00", result["accounts"][1]["marketValue"])

    def test_reported_total_requires_every_value_and_compatible_asof(self):
        cases = [{"reportedPnl": None}, {"holdingAsOf": "2026-10-27"},
                 {"holdingAsOf": DAY + "T14:01:00+08:00"}]
        for changes in cases:
            with self.subTest(changes=changes):
                rows = [position(), position("600002.SH", **changes)]
                result = build_portfolio(holdings(rows), {s: quote(s) for s in ("600001.SH", "600002.SH")}, DAY)
                self.assertIsNone(result["reportedPnl"])
                self.assertIsNone(result["pnlDifference"])
                self.assertEqual("468.00", result["computedPnl"])

    def test_same_source_in_different_timezones_can_sum_reported_values(self):
        rows = [position(holdingAsOf=DAY + "T14:00:00+08:00"),
                position("600002.SH", holdingAsOf=DAY + "T06:00:00Z")]
        result = build_portfolio(holdings(rows), {s: quote(s) for s in ("600001.SH", "600002.SH")}, DAY)
        self.assertEqual("380.02", result["reportedPnl"])

    def test_valuation_rejects_bad_date_unit_identity_adjustment_and_floats(self):
        cases = [
            {"symbol": "600002.SH"}, {"price": 12.34}, {"price": "NaN"}, {"price": "-1"},
            {"sourceTimestamp": "2026-10-27T15:00:00+08:00"},
            {"sourceTimestamp": "2026-10-29T15:00:00+08:00"},
            {"sourceTimestamp": DAY + "T15:00:00"},
            {"currency": "USD"}, {"priceUnit": "cents"}, {"adjustment": "qfq"},
            {"quality": {"status": "closing-snapshot", "unitVerified": False}},
            {"quality": {"status": "historical-snapshot", "unitVerified": True}},
            {"quality": {"status": "intraday-verified", "unitVerified": True, "intradaySignalAllowed": False}},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                report = build_portfolio(holdings(), {"600001.SH": quote(**changes)}, DAY)
                self.assertIsNone(report["marketValue"])
                self.assertIsNone(report["concentration"])
                self.assertEqual("190.01", report["positions"][0]["reportedPnl"])

    def test_nonbinary_arithmetic_and_rounding_only_at_display(self):
        source = holdings([position(quantity=3, cost="0.11", reportedPnl=None)])
        result = build_portfolio(source, {"600001.SH": quote(price="0.13")}, DAY)
        self.assertEqual("0.39", result["marketValue"])
        self.assertEqual("0.33", result["costValue"])
        self.assertEqual("0.06", result["computedPnl"])
        rows = [position(quantity=1, cost="0.1000", reportedPnl=None),
                position("600002.SH", quantity=1, cost="0.1000", reportedPnl=None)]
        result = build_portfolio(holdings(rows), {s: quote(s, price="0.105") for s in ("600001.SH", "600002.SH")}, DAY)
        self.assertEqual("0.21", result["marketValue"])
        self.assertEqual("0.01", result["computedPnl"])
        self.assertEqual("0.11", result["positions"][0]["marketValue"])

    def test_missing_broker_and_stale_holdings_never_claim_complete_current_account(self):
        result = build_portfolio(holdings(missingBrokers=["虚构券商乙"]), {"600001.SH": quote()}, DAY)
        self.assertEqual("partial-accounts", result["status"])
        self.assertIsNone(result["availableCash"])
        source = holdings([position(holdingAsOf="2026-10-27")])
        result = build_portfolio(source, {"600001.SH": quote()}, DAY)
        self.assertEqual("stale-snapshot", result["status"])
        self.assertFalse(result["positions"][0]["currentHoldingConfirmed"])

    def test_missing_holdings_contains_no_invented_positions_or_zero_amounts(self):
        result = build_portfolio(None, {}, DAY)
        self.assertEqual("missing", result["status"])
        self.assertEqual([], result["positions"])
        for key in ("marketValue", "pnl", "computedPnl", "reportedPnl", "availableCash", "concentration"):
            self.assertIsNone(result[key])


if __name__ == "__main__":
    unittest.main()
