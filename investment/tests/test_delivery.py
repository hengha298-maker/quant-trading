from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from investment.delivery import BJ, send_daily, _public_body

TEST_ROOT = Path(__file__).resolve().parents[1] / "work" / "delivery-unit-tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)


def temporary_root():
    return tempfile.TemporaryDirectory(prefix="fixture-", dir=TEST_ROOT)


NOW = datetime(2026, 10, 28, 14, 39, tzinfo=BJ)


def event(**changes):
    return {"format": "goose-investment-daily", "version": 1,
        "origin": "local-daily-pipeline", "validationMode": "live", "synthetic": False,
        "day": "2026-10-28", "completedAt": NOW.isoformat(), "reportTrusted": True,
        "code": "INTRADAY_VERIFIED", "holdingsStatus": "current-user-confirmed", **changes}


class Response:
    def __init__(self, data=b'{"code":200}', status=200):
        self.data, self.status = data, status
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, amount): return self.data[:amount]


class DeliveryTests(unittest.TestCase):
    def setup_config(self, root):
        config = Path(root)/"fictional-config.json"
        config.write_text(json.dumps({"server": "https://example.invalid", "device_key": "FictionalUnitTestKey"}), encoding="utf8")
        return config

    def test_default_neither_reads_config_nor_sends(self):
        with patch("investment.delivery._configuration", side_effect=AssertionError), patch("investment.delivery._default_opener", side_effect=AssertionError):
            result = send_daily(event(), config_path="must-not-read")
        self.assertEqual(result["code"], "EXPLICIT_SEND_APPROVAL_REQUIRED")

    def test_closed_summary_omits_private_fields(self):
        value = event()
        value.update(account="SECRET_ACCOUNT", quantity="887766", privateUrl="https://secret.invalid/KEY", cost="123456")
        body = _public_body(value, NOW)
        self.assertNotIn("SECRET", body)
        self.assertNotIn("887766", body)
        self.assertNotIn("secret.invalid", body)

    def test_synthetic_old_or_untrusted_does_not_read_config(self):
        for value in (event(synthetic=True), event(validationMode="injected-test"), event(reportTrusted=False),
                      event(day="2025-10-28"), event(code="raw error SECRET")):
            with patch("investment.delivery._configuration", side_effect=AssertionError):
                self.assertEqual(send_daily(value, confirmed=True, config_path="none", now=NOW)["status"], "blocked")

    def test_ack_not_phone_and_duplicate_date_different_id(self):
        calls = []
        def opener(request, **options):
            calls.append((request, options))
            return Response()
        with temporary_root() as root:
            config = self.setup_config(root)
            first = send_daily(event(reportId="one"), root, config, confirmed=True, opener=opener, now=NOW)
            second = send_daily(event(reportId="two"), root, config, confirmed=True, opener=opener, now=NOW)
            self.assertEqual(first["status"], "service-accepted")
            self.assertFalse(first["phoneArrivalVerified"])
            self.assertEqual(second["code"], "DAY_ALREADY_CLAIMED_NO_RESEND")
            self.assertEqual(len(calls), 1)
            payload = json.loads(calls[0][0].data)
            self.assertEqual(payload["device_key"], "FictionalUnitTestKey")
            # Device key is used only in the request, never persisted.
            logs = "".join(p.read_text(encoding="utf8") for p in (Path(root)/"investment").rglob("*.json"))
            self.assertNotIn("FictionalUnitTestKey", logs)

    def test_timeout_unknown_is_not_retried(self):
        calls = []
        def opener(request, **options):
            calls.append(1)
            raise TimeoutError("SECRET_NETWORK_DETAIL")
        with temporary_root() as root:
            config = self.setup_config(root)
            first = send_daily(event(), root, config, confirmed=True, opener=opener, now=NOW)
            again = send_daily(event(), root, config, confirmed=True, opener=opener, now=NOW)
            self.assertEqual(first["status"], "unknown")
            self.assertEqual(again["code"], "DAY_ALREADY_CLAIMED_NO_RESEND")
            self.assertEqual(len(calls), 1)
            self.assertNotIn("SECRET", str(first))

    def test_concurrent_senders_only_one_request(self):
        calls = []
        def opener(request, **options):
            calls.append(1)
            return Response()
        with temporary_root() as root:
            config = self.setup_config(root)
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: send_daily(event(), root, config, confirmed=True, opener=opener, now=NOW), range(3)))
            self.assertEqual(sum(r["sent"] for r in results), 1)
            self.assertEqual(len(calls), 1)

    def test_bad_response_and_plain_http_rejected(self):
        with temporary_root() as root:
            config = self.setup_config(root)
            config.write_text('{"server":"http://example.invalid","device_key":"fake"}', encoding="utf8")
            def opener(*args, **kwargs): raise AssertionError("Must not send")
            self.assertEqual(send_daily(event(), root, config, confirmed=True, opener=opener, now=NOW)["code"], "CONFIG_REJECTED")
            config.write_text('{"server":"https://example.invalid","device_key":"fake"}', encoding="utf8")
            self.assertEqual(send_daily(event(), root, config, confirmed=True, opener=lambda *a, **k: Response(b'{"code":500}'), now=NOW)["status"], "rejected")

    def test_late_send_does_not_read_credentials(self):
        late = datetime(2026,10,28,14,44,tzinfo=BJ)
        with patch("investment.delivery._configuration", side_effect=AssertionError):
            self.assertEqual(send_daily(event(), confirmed=True, config_path="none", now=late)["status"], "blocked")

    def test_deadline_after_config_blocks_request_and_keeps_claim(self):
        sequence = iter((NOW, datetime(2026,10,28,14,44,tzinfo=BJ)))
        def opener(*args, **kwargs): raise AssertionError("Past deadline")
        with temporary_root() as root:
            result = send_daily(event(), root, self.setup_config(root), confirmed=True, opener=opener, now=lambda: next(sequence))
            self.assertEqual(result["code"], "DEADLINE_PASSED_BEFORE_REQUEST")
            self.assertTrue(list(Path(root).rglob("*.claim.json")))

    def test_late_ack_is_not_claimed_on_time(self):
        sequence = iter((NOW, NOW, datetime(2026,10,28,14,44,tzinfo=BJ)))
        with temporary_root() as root:
            result = send_daily(event(), root, self.setup_config(root), confirmed=True,
                opener=lambda *a, **k: Response(), now=lambda: next(sequence))
            self.assertTrue(result["sent"])
            self.assertEqual(result["code"], "SERVICE_ACK_AFTER_DEADLINE")
            self.assertFalse(result["phoneArrivalVerified"])


if __name__ == "__main__":
    unittest.main()
