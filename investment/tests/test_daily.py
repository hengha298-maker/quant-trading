from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from investment.daily import BJ, latest_daily, run_daily, _report_health

TEST_ROOT = Path(__file__).resolve().parents[1] / "work" / "daily-unit-tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)


def temporary_root():
    # Isolate generated fixtures inside this workspace, including on Windows
    # AppContainer hosts whose generic Temp directory cannot be opened again.
    return tempfile.TemporaryDirectory(prefix="fixture-", dir=TEST_ROOT)


def clock(hour=14, minute=39, year=2026, month=10, day=28):
    return datetime(year, month, day, hour, minute, tzinfo=BJ)


def report(now, *, status="intraday-verified", holdings="current-user-confirmed"):
    instruments = []
    for symbol in ("600900.SH", "515450.SH"):
        instruments.append({"symbol": symbol, "quote": {"symbol": symbol,
            "source": "tencent-quote", "sourceTimestamp": (now-timedelta(seconds=2)).isoformat(),
            "price": "1.23", "currency": "CNY", "priceUnit": "CNY-per-share", "adjustment": "none",
            "quality": {"status": status, "unitVerified": True, "intradaySignalAllowed": status == "intraday-verified"}}})
    return {"format": "goose-live-research", "version": 1, "id": "fictional-unit-test",
        "generatedAt": now.isoformat(), "portfolio": {"status": holdings}, "instruments": instruments}


class DailyTests(unittest.TestCase):
    def test_planning_and_outside_window_never_call_market_or_claim(self):
        for now, planning in ((clock(21), False), (clock(), True), (clock(14, 44), False)):
            with temporary_root() as root:
                generate = Mock()
                result = run_daily(root, now=now, generate=generate, planning=planning)
                generate.assert_not_called()
                self.assertFalse(result["notification"]["sent"])
                self.assertFalse(list(Path(root).rglob("*.claim.json")))
                self.assertEqual(latest_daily(root)["attemptId"], result["attemptId"])

    def test_calendar_never_guesses_2027_or_weekends(self):
        for now, expected in ((clock(year=2027, month=1, day=4), "CALENDAR_UNVERIFIED"),
                              (clock(day=31), "NOT_A_TRADING_DAY")):
            with temporary_root() as root:
                generate = Mock()
                result = run_daily(root, now=now, generate=generate)
                self.assertEqual(result["code"], expected)
                generate.assert_not_called()

    def test_current_market_dryrun_keeps_stale_holding_warning(self):
        now = clock()
        with temporary_root() as root, patch("investment.daily.send_daily") as sender:
            generate = Mock(return_value=report(now, holdings="stale-snapshot"))
            result = run_daily(root, now=now, generate=generate)
            self.assertEqual(result["code"], "INTRADAY_VERIFIED")
            self.assertEqual(result["holdingsStatus"], "stale-snapshot")
            self.assertEqual(result["status"], "report-ready")
            self.assertEqual(result["notification"]["mode"], "dry-run")
            sender.assert_not_called()
            generate.assert_called_once_with(root, deadline=clock(14, 43).replace(second=30))

    def test_analysis_cutoff_reserves_time_for_failure_diagnostic(self):
        with temporary_root() as root:
            generate = Mock()
            result = run_daily(root, now=clock(14, 43).replace(second=40), generate=generate)
            generate.assert_not_called()
            self.assertEqual(result["code"], "DEADLINE_EXCEEDED")
            self.assertTrue(result["reportTrusted"])
            self.assertEqual(result["status"], "diagnostic")

    def test_no_replay_or_different_id_loophole_after_success(self):
        now = clock()
        with temporary_root() as root:
            generate = Mock(return_value=report(now))
            first = run_daily(root, now=now, generate=generate)
            second = run_daily(root, now=now, generate=generate)
            self.assertEqual(first["code"], "INTRADAY_VERIFIED")
            self.assertEqual(second["code"], "ALREADY_CLAIMED")
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(len(list(Path(root).rglob("*.json"))), 3)

    def test_concurrent_generation_has_one_winner(self):
        now = clock()
        with temporary_root() as root:
            generate = Mock(return_value=report(now))
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: run_daily(root, now=now, generate=generate), range(2)))
            self.assertEqual(sorted(r["code"] for r in results), ["ALREADY_CLAIMED", "INTRADAY_VERIFIED"])
            self.assertEqual(generate.call_count, 1)

    def test_failure_diagnostic_never_reuses_old_report_or_leaks_exception(self):
        with temporary_root() as root:
            generate = Mock(side_effect=RuntimeError("SECRET_ACCOUNT_887766"))
            result = run_daily(root, now=clock(), generate=generate)
            self.assertEqual(result["code"], "ANALYSIS_FAILED")
            self.assertIsNone(result["reportId"])
            contents = "".join(p.read_text(encoding="utf8") for p in Path(root).rglob("*.json"))
            self.assertNotIn("SECRET_ACCOUNT", contents)
            self.assertEqual(run_daily(root, now=clock(), generate=generate)["code"], "ALREADY_CLAIMED")

    def test_deadline_after_generation_blocks_success(self):
        sequence = iter((clock(), clock(), clock(14, 44), clock(14, 44)))
        with temporary_root() as root:
            result = run_daily(root, now=lambda: next(sequence), generate=lambda root, deadline: report(clock()))
            self.assertEqual(result["code"], "DEADLINE_EXCEEDED")
            self.assertFalse(result["notification"]["sent"])

    def test_historical_closing_synthetic_and_stale_never_pass(self):
        now = clock()
        for mutate in (lambda r: r.update(cachedReport=True), lambda r: r.update(synthetic=True),
                lambda r: r.update(generatedAt="2025-10-28T14:39:00+08:00"),
                lambda r: r["instruments"][0]["quote"]["quality"].update(status="closing-snapshot"),
                lambda r: r["instruments"][0]["quote"].update(sourceTimestamp=(now-timedelta(seconds=91)).isoformat()),
                lambda r: r["instruments"][0]["quote"].update(source="unreviewed"),
                lambda r: r["instruments"].pop(),
                lambda r: r["instruments"][0]["quote"]["quality"].update(unitVerified=False)):
            value = report(now)
            mutate(value)
            self.assertNotEqual(_report_health(value, now)[0], "INTRADAY_VERIFIED")

    def test_explicit_generator_deadline_flag_becomes_diagnostic(self):
        value = report(clock())
        value["deadlineMissed"] = True
        self.assertEqual(_report_health(value, clock()), ("DEADLINE_EXCEEDED", True, "current-user-confirmed"))


if __name__ == "__main__":
    unittest.main()
