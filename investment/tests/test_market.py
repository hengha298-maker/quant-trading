import importlib.util
from datetime import datetime
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from investment.market import BJ, parse_tencent, parse_eastmoney, classify_quote, _daily_parser, collect_market
import json


def etf_response(price="1.319", name="红利低波50ETF"):
    fields = [""] * 88
    for key, value in {0:"1", 1:name, 2:"515450", 3:price, 4:"1.320", 5:"1.310", 6:"1000",
            30:"20261028144400", 33:"1.330", 34:"1.300", 36:"1000"}.items():
        fields[key] = value
    return ('v_sh515450="' + '~'.join(fields) + '";').encode('gbk')


def observation(stamp="2026-10-28T14:44:01+08:00"):
    return {"requestedAt": "2026-10-28T14:44:00+08:00", "receivedAt": stamp,
        "elapsedMs": 100, "rawSha256": "0" * 64}


class MarketTests(unittest.TestCase):
    def test_etf_tick_volume_and_literal_prices(self):
        quote = parse_tencent(etf_response(), "515450.SH")
        self.assertEqual(quote["price"], "1.319")
        self.assertEqual(quote["volume"], 100000)
        self.assertFalse(quote["independentFeedVerified"])

    def test_reject_wrong_identity_and_subtick(self):
        for raw in (etf_response(name="另一基金"), etf_response(price="1.3191"), etf_response(price="13.190")):
            with self.assertRaises(ValueError):
                parse_tencent(raw, "515450.SH")

    def test_no_postclose_intraday_signal(self):
        quote = parse_tencent(etf_response(), "515450.SH")
        quote["sourceTimestamp"] = "2026-10-28T15:00:01+08:00"
        obs = observation("2026-10-28T21:00:00+08:00")
        result = classify_quote(quote, obs, datetime(2026, 10, 28, 21, 0, 1, tzinfo=BJ))
        self.assertEqual(result["quality"]["status"], "closing-snapshot")
        self.assertFalse(result["quality"]["intradaySignalAllowed"])

    def test_current_and_stale_intraday_are_distinct(self):
        quote = parse_tencent(etf_response(), "515450.SH")
        fresh = classify_quote(quote, observation(), datetime(2026, 10, 28, 14, 44, 2, tzinfo=BJ))
        self.assertTrue(fresh["quality"]["intradaySignalAllowed"])
        stale = classify_quote(quote, observation(), datetime(2026, 10, 28, 14, 46, tzinfo=BJ))
        self.assertEqual(stale["quality"]["status"], "blocked")

    def test_2027_calendar_is_blocked(self):
        quote = parse_tencent(etf_response(), "515450.SH")
        quote["sourceTimestamp"] = "2027-01-04T14:44:00+08:00"
        obs = {**observation(), "requestedAt": "2027-01-04T14:44:01+08:00", "receivedAt": "2027-01-04T14:44:02+08:00"}
        result = classify_quote(quote, obs, datetime(2027, 1, 4, 14, 44, 3, tzinfo=BJ))
        self.assertFalse(result["quality"]["intradaySignalAllowed"])
        self.assertIn("CALENDAR_UNVERIFIED_STOP", result["quality"]["codes"])

    def test_daily_adjustment_key_cannot_silently_fallback(self):
        raw = b'goose={"code":0,"data":{"sh600900":{"qfqday":[["2026-10-28","29.00","29.40","29.50","28.80","1000"]]}}};'
        with self.assertRaisesRegex(ValueError, "BASIS_OR_SYMBOL"):
            _daily_parser(raw, "600900.SH", "2026-10-28", "2026-10-28", "none")
        self.assertEqual(len(_daily_parser(raw, "600900.SH", "2026-10-28", "2026-10-28", "qfq")), 1)

    def test_adjusted_daily_duplicate_and_ohlc_rejected(self):
        row = ["2026-10-28", "29.00", "29.40", "29.50", "28.80", "1000"]
        def response(rows):
            return json.dumps({"code": 0, "data": {"sh600900": {"qfqday": rows}}}).encode()
        with self.assertRaisesRegex(ValueError, "GAP_DUPLICATE"):
            _daily_parser(response([row, row]), "600900.SH", "2026-10-28", "2026-10-28", "qfq")
        with self.assertRaisesRegex(ValueError, "OHLC"):
            _daily_parser(response([["2026-10-28", "29.00", "29.40", "29.20", "28.80", "1000"]]), "600900.SH", "2026-10-28", "2026-10-28", "qfq")

    def test_etf_minute_decimal_identity_and_no_rescale(self):
        data = {"rc": 0, "data": {"code": "515450", "market": 1, "decimal": 3,
            "name": "红利低波50ETF南方", "prePrice": "1.454", "trends":
                ["2026-10-28 15:00,1.449,1.449,1.449,1.449,100,14490,1.449"]}}
        quote = parse_eastmoney(json.dumps(data, ensure_ascii=False).encode(), "515450.SH")
        self.assertEqual(quote["price"], "1.449")
        data["data"]["decimal"] = 2
        with self.assertRaisesRegex(ValueError, "DECIMAL_MISMATCH"):
            parse_eastmoney(json.dumps(data).encode(), "515450.SH")

    def test_unverified_calendar_stops_before_output_and_network(self):
        with self.assertRaisesRegex(ValueError, "CALENDAR_RANGE_UNSUPPORTED"):
            collect_market(["600900.SH"], ROOT / "work" / "must-not-be-created-calendar-test",
                now=datetime(2027, 1, 4, 14, 44, tzinfo=BJ))
        self.assertFalse((ROOT / "work" / "must-not-be-created-calendar-test").exists())


if __name__ == "__main__":
    unittest.main()
