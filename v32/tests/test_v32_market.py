"""Offline safety contracts; all upstream responses and calendar calls are fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
import sys
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import URLError

from goose_research import market

SESSIONS = ["2026-10-19", "2026-10-20", "2026-10-21"]


def dataset():
    return {"format": "goose-market-dataset", "version": 1, "symbol": "600900.SH", "kind": "stock", "source": "fictional-offline-test", "sourceUrl": "https://example.invalid/fictional", "adjustment": "none", "currency": "CNY", "priceUnit": "CNY-per-share", "fetchedAt": "2026-10-23T16:00:00+08:00", "calendar": {"name": "XSHG", "sessions": SESSIONS[:], "supportedThrough": "2026-12-31"}, "bars": [{"date": day, "open": "20.10", "high": "20.50", "low": "20.00", "close": "20.25", "volume": 10000, "publishedAt": None} for day in SESSIONS], "quality": {"status": "synthetic", "crossValidated": False, "warnings": [], "unitVerified": True, "corporateActionsVerified": False}}


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.calendar = patch.object(market, "xshg_sessions", return_value=SESSIONS[:])
        self.calendar.start(); self.addCleanup(self.calendar.stop)

    def test_normalization_is_copy_and_marks_unverified_corporate_actions_and_source_time(self):
        original = dataset(); expected = deepcopy(original)
        result = market.validate_dataset(original)
        self.assertEqual(original, expected)
        self.assertIsNone(result["bars"][0]["publishedAt"])
        self.assertEqual(result["bars"][0]["close"], "20.25")
        self.assertIn("CORPORATE_ACTIONS_UNVERIFIED_NO_TOTAL_RETURN_CLAIM", result["quality"]["warnings"])
        self.assertIn("SOURCE_PUBLICATION_TIME_UNKNOWN_USE_NEXT_SESSION_ONLY", result["quality"]["warnings"])
        self.assertIn("SYNTHETIC_ENGINEERING_FIXTURE_NOT_REAL_MARKET_EVIDENCE", result["quality"]["warnings"])
        self.assertEqual(market.validate_dataset(result), result)

    def test_ohlc_float_invalid_prices_units_and_volume_are_refused(self):
        for key, value in [("open", 20.1), ("close", "0"), ("high", "NaN"), ("low", "2e1"), ("high", "19.00"), ("volume", True), ("volume", -1), ("volume", 1.5)]:
            doc = dataset(); doc["bars"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): market.validate_dataset(doc)
        for key, value in [("adjustment", "qfq"), ("currency", "USD"), ("priceUnit", "fen"), ("version", True), ("version", 2), ("sourceUrl", "http://example.invalid"), ("sourceUrl", "https://user:password@example.invalid")]:
            doc = dataset(); doc[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): market.validate_dataset(doc)

    def test_missing_duplicate_unsorted_or_forged_calendar_sessions_are_refused(self):
        mutations = [lambda d: d["bars"].pop(), lambda d: d["bars"].reverse(), lambda d: d["bars"][1].update(date=SESSIONS[0]), lambda d: d["calendar"]["sessions"].reverse(), lambda d: d["calendar"]["sessions"].pop(1)]
        for mutate in mutations:
            doc = dataset(); mutate(doc)
            with self.assertRaises(ValueError): market.validate_dataset(doc)
        doc = dataset(); doc["calendar"]["sessions"] = ["2026-10-19", "2026-10-21"]; doc["bars"].pop(1)
        with self.assertRaisesRegex(ValueError, "CALENDAR_SESSION_MISSING"): market.validate_dataset(doc)

    def test_aware_source_times_are_required_and_fetch_time_is_not_a_publication_substitute(self):
        for value in ["2026-10-19T15:00:00", "2026-10-18T15:00:00+08:00", "2026-10-24T15:00:00+08:00"]:
            doc = dataset(); doc["bars"][0]["publishedAt"] = value
            with self.assertRaises(ValueError): market.validate_dataset(doc)
        doc = dataset(); doc["bars"][0]["publishedAt"] = "2026-10-19T15:02:00+08:00"
        self.assertEqual(market.validate_dataset(doc)["bars"][0]["publishedAt"], doc["bars"][0]["publishedAt"])
        doc = dataset(); doc["fetchedAt"] = "9999-12-31T23:59:59+08:00"
        with self.assertRaisesRegex(ValueError, "FUTURE"): market.validate_dataset(doc)

    def test_calendar_boundary_and_future_bars_fail_closed(self):
        doc = dataset(); doc["calendar"]["supportedThrough"] = "2027-12-31"
        with self.assertRaises(ValueError): market.validate_dataset(doc)
        doc = dataset(); doc["fetchedAt"] = "2026-10-20T09:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "FUTURE"): market.validate_dataset(doc)

    def test_etf_cannot_be_promoted_with_missing_unit_evidence_or_price_rescaling(self):
        doc = dataset(); doc.update(symbol="515450.SH", kind="etf"); doc["quality"].update(status="validated", unitVerified=False)
        with self.assertRaises(ValueError): market.validate_dataset(doc)
        doc["quality"]["unitVerified"] = True
        with self.assertRaisesRegex(ValueError, "UNIT_EVIDENCE"): market.validate_dataset(doc)
        doc["quality"].update(status="blocked", unitVerified=False)
        result = market.validate_dataset(doc)
        self.assertEqual(result["bars"][0]["close"], "20.25")
        self.assertEqual(result["quality"]["status"], "blocked")
        doc["quality"].update(status="validated", unitVerified=True, unitEvidence="Fictional unit-test evidence only; not real verification")
        self.assertTrue(market.validate_dataset(doc)["quality"]["unitVerified"])

    def test_quality_boolean_flags_and_unknown_fields_are_not_silently_accepted(self):
        for field in ["crossValidated", "unitVerified", "corporateActionsVerified"]:
            doc = dataset(); doc["quality"][field] = "true"
            with self.assertRaises(ValueError): market.validate_dataset(doc)
        doc = dataset(); doc["brokerPassword"] = "fictional"
        with self.assertRaises(ValueError): market.validate_dataset(doc)

    def test_etf_tenfold_jump_is_flagged_without_rescaling_and_real_validated_input_is_refused(self):
        doc = dataset(); doc.update(symbol="515450.SH", kind="etf")
        for row, price in zip(doc["bars"], ["1.000", "10.000", "10.000"]):
            row.update({field: price for field in ("open", "high", "low", "close")})
        original = deepcopy(doc)
        diagnostics = market.etf_anomaly_diagnostics(doc)
        self.assertEqual(doc, original)
        self.assertEqual(diagnostics["maxAbsChangeBps"], "90000.000000")
        self.assertEqual(diagnostics["largeChangeDates"], [SESSIONS[1]])
        self.assertEqual(diagnostics["suspectedScaleDates"], [SESSIONS[1]])
        self.assertEqual(diagnostics["status"], "blocked")
        self.assertIn("SYNTHETIC_ENGINEERING_FIXTURE_NOT_REAL_MARKET_EVIDENCE", diagnostics["warnings"])
        self.assertEqual(market.validate_dataset(doc)["bars"][1]["close"], "10.000")
        for next_price in ("100.000", "0.100", "0.010"):
            variant = deepcopy(original)
            for row in variant["bars"][1:]:
                row.update({field: next_price for field in ("open", "high", "low", "close")})
            self.assertEqual(market.etf_anomaly_diagnostics(variant)["suspectedScaleDates"], [SESSIONS[1]])
        doc["quality"].update(status="validated", unitEvidence="Fictional unit test declaration")
        with self.assertRaisesRegex(ValueError, "ANOMALY"): market.validate_dataset(doc)

    def test_515450_tick_mismatch_is_flagged_even_when_daily_close_change_is_small(self):
        doc = dataset(); doc.update(symbol="515450.SH", kind="etf")
        for bar in doc["bars"]:
            bar.update(open="1.000", high="1.002", low="0.999", close="1.001")
        doc["bars"][1]["open"] = "1.0001"
        diagnostics = market.etf_anomaly_diagnostics(doc)
        self.assertEqual(diagnostics["tickMismatchDates"], [SESSIONS[1]])
        self.assertEqual(diagnostics["largeChangeDates"], [])
        self.assertEqual(diagnostics["status"], "blocked")
        doc["quality"].update(status="validated", unitEvidence="Fictional unit test declaration")
        with self.assertRaisesRegex(ValueError, "ANOMALY"): market.validate_dataset(doc)

    def test_real_response_excerpt_never_becomes_unit_verified_and_input_remains_unchanged(self):
        doc = dataset(); doc.update(symbol="515450.SH", kind="etf")
        doc["quality"].update(status="blocked", unitVerified=False)
        # Public 2025 historical response excerpt, observed 2026-10-26 from
        # both providers. Its agreement is not proof of ETF identity/units.
        # No private holdings and no online/filesystem dependency in this test.
        doc["bars"] = [
            {"date": "2025-04-03", "open": "1.419", "close": "1.425", "high": "1.427", "low": "1.411", "volume": 64698100, "publishedAt": None},
            {"date": "2025-04-07", "open": "1.406", "close": "1.362", "high": "1.409", "low": "1.329", "volume": 139748700, "publishedAt": None},
            {"date": "2025-04-08", "open": "1.360", "close": "1.392", "high": "1.394", "low": "1.360", "volume": 124953500, "publishedAt": None},
        ]
        original = deepcopy(doc)
        diagnostics = market.etf_anomaly_diagnostics(doc)
        self.assertEqual(diagnostics["maxAbsChangeBps"], "442.105263")
        self.assertEqual(diagnostics["largeChangeDates"], [])
        self.assertEqual(diagnostics["suspectedScaleDates"], [])
        self.assertEqual(diagnostics["tickMismatchDates"], [])
        self.assertEqual(diagnostics["status"], "blocked")
        self.assertIn("ETF_PRICE_UNIT_NOT_INDEPENDENTLY_VERIFIED", diagnostics["warnings"])
        self.assertFalse(doc["quality"]["unitVerified"])
        self.assertEqual(doc, original)


class CalendarTests(unittest.TestCase):
    def test_missing_dependency_never_falls_back_to_weekdays(self):
        with patch.dict(sys.modules, {"exchange_calendars": None}):
            with self.assertRaisesRegex(ValueError, "DEPENDENCY_REQUIRED"): market.xshg_sessions("2026-10-19", "2026-10-21")

    def test_after_reviewed_calendar_boundary_is_rejected_before_import(self):
        with self.assertRaisesRegex(ValueError, "2026_12_31"): market.xshg_sessions("2026-12-31", "2027-01-04")
        with self.assertRaises(ValueError): market.xshg_sessions("2026-02-30", "2026-03-02")
        with self.assertRaises(ValueError): market.xshg_sessions("2026-10-21", "2026-10-19")


class NetworkAdapterTests(unittest.TestCase):
    def test_eastmoney_exact_decimal_and_lot_conversion_keep_unknown_release_time(self):
        response = {"data": {"code": "600900", "klines": [f"{day},20.10,20.25,20.50,20.00,123" for day in SESSIONS]}}
        with patch.object(market, "xshg_sessions", return_value=SESSIONS[:]), patch.object(market, "_read_json", return_value=response) as network:
            doc = market.fetch_history("600900.SH", SESSIONS[0], SESSIONS[-1])
        self.assertEqual(network.call_count, 1)
        self.assertEqual(doc["bars"][0]["volume"], 12300)
        self.assertEqual(doc["bars"][0]["close"], "20.25")
        self.assertIsNone(doc["bars"][0]["publishedAt"])
        self.assertFalse(doc["quality"]["crossValidated"]); self.assertFalse(doc["quality"]["corporateActionsVerified"])

    def test_etf_response_stays_blocked_without_unit_repair(self):
        response = {"data": {"code": "515450", "klines": [f"{day},200.10,200.25,200.50,200.00,123" for day in SESSIONS]}}
        with patch.object(market, "xshg_sessions", return_value=SESSIONS[:]), patch.object(market, "_read_json", return_value=response):
            doc = market.fetch_history("515450.SH", SESSIONS[0], SESSIONS[-1])
        self.assertEqual(doc["quality"]["status"], "blocked"); self.assertFalse(doc["quality"]["unitVerified"])
        self.assertEqual(doc["bars"][0]["close"], "200.25")

    def test_unreviewed_instrument_needs_explicit_kind_and_remains_blocked(self):
        with self.assertRaisesRegex(ValueError, "EXPLICIT_KIND_REQUIRED"):
            market.fetch_history("000001.SZ", SESSIONS[0], SESSIONS[-1])
        with self.assertRaisesRegex(ValueError, "KIND_CONFLICT"):
            market.fetch_history("600900.SH", SESSIONS[0], SESSIONS[-1], kind="etf")
        response = {"data": {"code": "000001", "klines": [f"{day},20.10,20.25,20.50,20.00,123" for day in SESSIONS]}}
        with patch.object(market, "xshg_sessions", return_value=SESSIONS[:]), patch.object(market, "_read_json", return_value=response):
            doc = market.fetch_history("000001.SZ", SESSIONS[0], SESSIONS[-1], kind="stock")
        self.assertEqual(doc["kind"], "stock"); self.assertEqual(doc["quality"]["status"], "blocked")
        self.assertIn("INSTRUMENT_KIND_USER_DECLARED_IDENTITY_AND_UNITS_NOT_VERIFIED", doc["quality"]["warnings"])

    def test_tencent_requires_unadjusted_day_and_never_silently_falls_back(self):
        raw = {"data": {"sh600900": {"day": [[day, "20.10", "20.25", "20.50", "20.00", "123"] for day in SESSIONS]}}}
        with patch.object(market, "xshg_sessions", return_value=SESSIONS[:]), patch.object(market, "_read_json", return_value=raw):
            doc = market.fetch_history("600900.SH", SESSIONS[0], SESSIONS[-1], "tencent")
        self.assertEqual(doc["source"], "tencent"); self.assertEqual(doc["bars"][0]["volume"], 12300)
        raw["data"]["sh600900"] = {"qfqday": []}
        with patch.object(market, "xshg_sessions", return_value=SESSIONS[:]), patch.object(market, "_read_json", return_value=raw):
            with self.assertRaisesRegex(ValueError, "UNADJUSTED"): market.fetch_history("600900.SH", SESSIONS[0], SESSIONS[-1], "tencent")
        with self.assertRaisesRegex(ValueError, "NO_SILENT_FALLBACK"): market.fetch_history("600900.SH", SESSIONS[0], SESSIONS[-1], "sina")

    def test_network_proxy_tls_errors_are_redacted_and_no_retry_is_attempted(self):
        opener = MagicMock(); opener.open.side_effect = URLError("fictional secret proxy credential never print")
        with patch.object(market, "build_opener", return_value=opener) as build:
            with self.assertRaisesRegex(ValueError, "^NETWORK_UNAVAILABLE_PROXY_OR_TLS_CHECK_REQUIRED$"):
                market._read_json(market.EASTMONEY_URL)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)
        handlers = build.call_args.args
        https = next(handler for handler in handlers if isinstance(handler, market.HTTPSHandler))
        self.assertTrue(https._context.check_hostname)
        self.assertEqual(https._context.verify_mode, market.ssl.CERT_REQUIRED)

    def test_rejected_redirects_do_not_downgrade_https_or_cross_origin(self):
        guard = market._SameHttpsOrigin(); req = market.Request(market.EASTMONEY_URL)
        for url in ["http://push2his.eastmoney.com/x", "https://example.invalid/x", "https://user:password@push2his.eastmoney.com/x"]:
            with self.assertRaisesRegex(ValueError, "REDIRECT_NOT_ALLOWED"):
                guard.redirect_request(req, None, 302, "found", {}, url)

    def test_upstream_duplicate_json_keys_are_rejected_without_evaluating_javascript(self):
        response = MagicMock(); response.__enter__.return_value = response
        response.geturl.return_value = market.EASTMONEY_URL
        opener = MagicMock(); opener.open.return_value = response
        for body in [b'{"data":1,"data":2}', b'{"__proto__":{}}', b'goose=alert("not executed")']:
            response.read.return_value = body
            with patch.object(market, "build_opener", return_value=opener):
                with self.assertRaises(ValueError): market._read_json(market.EASTMONEY_URL)


if __name__ == "__main__":
    unittest.main()
