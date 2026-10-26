"""No network. Every authorized send uses a fake opener and fake key file."""
from copy import deepcopy
import json
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import notify


def health():
    return {"format":"goose-pre1445-test", "version":1,
        "dataHealth":{"symbol":"600900.SH","through":"2025-12-31"},
        "strategyObservation":{"stance":"watch","decisionAllowed":False},
        "notification":{"mode":"dry-run","sent":False,"phoneArrivalVerified":False},
        "codes":["TEST_ONLY_NO_BARK_SENT","NO_TODAY_EXECUTABLE_SIGNAL"]}


class Reply:
    def __init__(self, status=200, raw=b'{"code":200}'):
        self.status, self.raw = status, raw

    def __enter__(self):
        return self

    def __exit__(self, *_arguments):
        return False

    def read(self, limit):
        return self.raw[:limit]


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".notify-fiction-", dir=Path(__file__).resolve().parent)
        self.config = Path(self.temporary.name)/"fictional-bark.json"
        self.config.write_text(json.dumps({"server":"https://bark.example.invalid/","device_key":"FICTIONAL_KEY_ONLY"}),encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def send(self, report=None, response=None):
        transport = Mock()
        transport.open.return_value = response or Reply()
        result = notify.send_manual_test(report or health(),self.config,confirmed=True,opener=transport)
        return result,transport

    def test_no_confirmation_causes_zero_config_or_network_io(self):
        for confirmation in (False,None,1,"yes"):
            with self.subTest(confirmation=confirmation), patch.object(Path,"open",side_effect=AssertionError("must not read config")), patch.object(notify,"_default_opener",side_effect=AssertionError("must not create transport")):
                transport=Mock(side_effect=AssertionError("must not call transport"))
                result=notify.send_manual_test(object(),object(),confirmed=confirmation,opener=transport)
                self.assertEqual(result["code"],"MANUAL_TEST_CONFIRMATION_REQUIRED")
                transport.assert_not_called()
                transport.open.assert_not_called()

    def test_acceptance_puts_key_only_in_json_body_and_never_claims_phone_arrival(self):
        report=health();before=deepcopy(report)
        result,transport=self.send(report)
        self.assertEqual(result,{"status":"service-accepted","phoneArrivalVerified":False})
        transport.open.assert_called_once()
        request=transport.open.call_args.args[0]
        self.assertEqual(request.full_url,"https://bark.example.invalid/push")
        self.assertNotIn("FICTIONAL_KEY_ONLY",request.full_url)
        self.assertNotIn("FICTIONAL_KEY_ONLY",repr(request.headers))
        self.assertEqual(transport.open.call_args.kwargs,{"timeout":8})
        self.assertEqual(request.get_method(),"POST")
        body=json.loads(request.data)
        self.assertEqual(body["device_key"],"FICTIONAL_KEY_ONLY")
        self.assertEqual(body["title"],"养鹅计划V3.2/单次测试")
        self.assertEqual(body["group"],"GooseResearch-Test")
        self.assertEqual(body["isArchive"],1)
        self.assertEqual(set(body),{"device_key","title","body","group","isArchive"})
        self.assertEqual(report,before)

    def test_non_https_non_root_credentials_queries_fragments_and_unsafe_keys_are_refused(self):
        invalid=[{"server":value,"device_key":"FICTIONAL_KEY_ONLY"} for value in [
            "http://bark.example.invalid","https://user:pass@bark.example.invalid/",
            "https://bark.example.invalid/a-key","https://bark.example.invalid/?key=x",
            "https://bark.example.invalid/#x","https://bark.example.invalid/?",
            "https://bark.example.invalid:",
            "https://127.0.0.1/","https://[::1]/","https://bark.example.invalid/\n",
        ]]
        invalid += [{"server":"https://bark.example.invalid","device_key":"bad/key"},
            {"server":"https://bark.example.invalid","device_key":"FICTIONAL_KEY_ONLY","is_archive":1},
            {"server":"https://bark.example.invalid","device_key":"FICTIONAL_KEY_ONLY","body":"PRIVATE_OVERRIDE"}]
        for value in invalid:
            with self.subTest(value=value):
                self.config.write_text(json.dumps(value),encoding="utf-8")
                result,transport=self.send()
                self.assertEqual(result["code"],"MANUAL_TEST_CONFIG_REJECTED")
                transport.open.assert_not_called()

    def test_health_permission_or_secret_payload_injection_is_rejected_before_config_read(self):
        mutations=[lambda x:x.update(format="wrong"),lambda x:x["notification"].update(sent=True),
            lambda x:x["notification"].update(mode="send"),lambda x:x["strategyObservation"].update(stance="accumulate"),
            lambda x:x["strategyObservation"].update(decisionAllowed=True),lambda x:x.update(codes=["FICTIONAL_PRIVATE_TOKEN"]),
            lambda x:x["dataHealth"].update(symbol="600900.SH PRIVATE_HOLDINGS"),lambda x:x["dataHealth"].update(through="2025-12-31 SECRET")]
        for change in mutations:
            report=health();change(report)
            with patch.object(Path,"open",side_effect=AssertionError("rejected health must not read key")):
                result,transport=self.send(report)
            self.assertEqual(result["code"],"MANUAL_TEST_HEALTH_REJECTED")
            transport.open.assert_not_called()

    def test_free_text_previews_account_fields_and_environment_never_enter_outgoing_body(self):
        report=health();report["barkPreview"]={"title":"PRIVATE_TITLE","body":"PRIVATE_HOLDINGS_AMOUNT_RAW_EXCEPTION"}
        report["dataHealth"]["source"]="PRIVATE_ACCOUNT_SOURCE"
        with patch.dict("os.environ",{"BARK_SERVER":"https://unrelated.invalid","BARK_KEY":"OLD_PRIVATE_KEY"}):
            result,transport=self.send(report)
        self.assertEqual(result["status"],"service-accepted")
        request=transport.open.call_args.args[0]
        payload=json.loads(request.data)
        for forbidden in ("PRIVATE_","OLD_PRIVATE_KEY","unrelated.invalid"):
            self.assertNotIn(forbidden,payload["body"])
        self.assertEqual(request.full_url,"https://bark.example.invalid/push")

    def test_redirect_http_error_and_exception_are_sanitized_without_retry(self):
        for response in (Reply(302),Reply(500)):
            result,transport=self.send(response=response)
            self.assertEqual(result["code"],"MANUAL_TEST_HTTP_REJECTED")
            transport.open.assert_called_once()
        failures=[HTTPError("https://SECRET_HOST/SECRET_KEY",302,"PRIVATE_RAW",{},None),
            URLError("PRIVATE_RAW_EXCEPTION"),ssl.SSLCertVerificationError("PRIVATE_CERT_DETAILS")]
        for error in failures:
            transport=Mock();transport.open.side_effect=error
            result=notify.send_manual_test(health(),self.config,confirmed=True,opener=transport)
            transport.open.assert_called_once()
            self.assertEqual(result["status"],"failed")
            self.assertFalse(result["phoneArrivalVerified"])
            for forbidden in ("SECRET","PRIVATE","https://","FICTIONAL_KEY_ONLY"):
                self.assertNotIn(forbidden,json.dumps(result))

    def test_http_200_requires_valid_exact_service_success_code(self):
        for raw in (b'{"code":400}',b'{"code":200.0}',b'{"code":true}',b'{"code":200,"code":400}',b'PRIVATE_RAW',b'x'*65537):
            result,transport=self.send(response=Reply(raw=raw))
            self.assertEqual(result["status"],"failed")
            transport.open.assert_called_once()
            self.assertNotIn("PRIVATE_RAW",json.dumps(result))

    def test_archive_false_is_respected_without_environment_fallback(self):
        self.config.write_text(json.dumps({"server":"https://bark.example.invalid","device_key":"FICTIONAL_KEY_ONLY","is_archive":False}),encoding="utf-8")
        with patch.dict("os.environ",{"BARK_IS_ARCHIVE":"true"}):
            result,transport=self.send()
        self.assertEqual(result["status"],"service-accepted")
        self.assertEqual(json.loads(transport.open.call_args.args[0].data)["isArchive"],0)

    def test_bad_or_deeply_nested_configuration_is_sanitized_before_transport(self):
        for raw in (b'{"device_key":"FICTIONAL_KEY_ONLY","server":', b'['*5000+b']'*5000,
            b'{"server":"https://bark.example.invalid","server":"https://other.invalid","device_key":"FICTIONAL_KEY_ONLY"}'):
            self.config.write_bytes(raw)
            result,transport=self.send()
            self.assertEqual(result["code"],"MANUAL_TEST_CONFIG_REJECTED")
            transport.open.assert_not_called()
            self.assertNotIn("FICTIONAL_KEY_ONLY",json.dumps(result))

    def test_default_transport_keeps_tls_verification_and_blocks_redirects(self):
        with patch.object(notify,"build_opener") as build:
            notify._default_opener()
        handlers=build.call_args.args
        context=next(handler._context for handler in handlers if isinstance(handler,notify.HTTPSHandler))
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        redirect=next(handler for handler in handlers if isinstance(handler,notify._NoRedirect))
        self.assertIsNone(redirect.redirect_request(None,None,302,"",{},"https://another.invalid"))


if __name__=="__main__":
    unittest.main()
