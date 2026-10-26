"""Loopback-only tests using temporary fictitious resources, never user data."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from http.client import HTTPConnection
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve import HOST, RESOURCES, make_server, resource_name
from launch import LauncherError, own_dns_name, port_in_use, route_is_ours, tailscale_json, other_routes


class StaticServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".goose-v3-server-test-", dir=Path(__file__).resolve().parent)
        self.root = Path(self.temp.name) / "web"
        self.root.mkdir()
        for name in RESOURCES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fictional:" + name).encode("utf-8"))
        (self.root / "private.json").write_text("FICTIONAL_PRIVATE_SECRET", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("FICTIONAL_GIT_SECRET", encoding="utf-8")
        self.server = make_server(port=0, web_root=self.root)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)
        self.temp.cleanup()

    def request(self, method="GET", path="/", body=None, headers=None):
        connection = HTTPConnection(HOST, self.server.server_port, timeout=4)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_only_loopback_is_bound(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_every_allowlisted_resource_and_mime(self):
        for name, mime in RESOURCES.items():
            with self.subTest(name=name):
                status, headers, body = self.request(path="/" + name)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], mime)
                self.assertEqual(body, ("fictional:" + name).encode())

    def test_root_and_head(self):
        self.assertEqual(self.request()[2], b"fictional:index.html")
        status, headers, body = self.request(method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), len(b"fictional:index.html"))

    def test_security_headers_and_sw_updates(self):
        status, headers, _ = self.request(path="/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("no-cache", headers["Cache-Control"])
        csp = headers["Content-Security-Policy"]
        for expected in ("script-src 'self'", "form-action 'none'", "frame-ancestors 'none'"):
            self.assertIn(expected, csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_no_git_private_directory_or_traversal_access(self):
        targets = [
            "/private.json", "/.git/config", "/icons/", "/../private.json",
            "/%2e%2e/private.json", "/icons/../../private.json", "/%2fprivate.json",
            "/%5cprivate.json", "/index.html%00", "/%252e%252e/private.json",
            "//attacker.invalid/index.html", "/index.html/", "/INDEX.HTML",
            "/%FF", "/bad%", "/index.html#fragment", "http://attacker.invalid/index.html",
        ]
        for target in targets:
            with self.subTest(target=target):
                status, _, body = self.request(path=target)
                self.assertEqual(status, 404)
                self.assertEqual(body, b"404\n")

    def test_query_does_not_select_another_file(self):
        self.assertEqual(self.request(path="/index.html?file=private.json")[2], b"fictional:index.html")

    def test_write_and_other_methods_are_rejected(self):
        for method in ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT", "PROPFIND"]:
            with self.subTest(method=method):
                status, _, body = self.request(method, "/index.html", body=b"FICTIONAL_UPLOAD")
                self.assertEqual(status, 405)
                self.assertEqual(body, b"405\n")
        self.assertEqual((self.root / "index.html").read_bytes(), b"fictional:index.html")

    def test_get_request_body_is_rejected(self):
        self.assertEqual(self.request(body=b"FICTIONAL_UPLOAD")[0], 400)
        self.assertEqual(self.request(headers={"Transfer-Encoding": "chunked"})[0], 400)

    def test_requests_leave_no_access_log(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.request(path="/index.html?fictional-secret=never-log")
            self.request(path="/private.json")
            self.request(method="POST", body=b"FICTIONAL_UPLOAD")
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")

    def test_symlink_resource_is_not_served(self):
        target = self.root / "index.html"
        target.unlink()
        # Mock the filesystem classification instead of requiring Windows
        # developer-mode privilege to create a real symbolic link.
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == target or original(path)):
            self.assertEqual(self.request()[0], 404)


class RoutingTests(unittest.TestCase):
    def test_null_serve_status_is_empty_but_null_device_status_is_rejected(self):
        from types import SimpleNamespace
        with patch.object(Path,'is_file', return_value=True), patch('launch.subprocess.run', return_value=SimpleNamespace(returncode=0,stdout=b'null')):
            self.assertEqual(tailscale_json('serve','status','--json'), {})
            with self.assertRaises(LauncherError): tailscale_json('status','--json')

    def test_other_ports_are_preserved_including_foreground_routes(self):
        old={'Foreground':{'old-session':{'TCP':{'8443':{'HTTPS':True}},'Web':{'fixture.ts.net:8443':{'Handlers':{'/':{'Proxy':'http://127.0.0.1:1234'}}}}}}}
        import copy
        current=copy.deepcopy(old)
        current['Foreground']['new-session']={'TCP':{'10001':{'HTTPS':True}},'Web':{'fixture.ts.net:10001':{'Handlers':{'/':{'Proxy':'http://127.0.0.1:9394'}}}}}
        current['ETag']='updated'
        self.assertEqual(other_routes(old),other_routes(current))
        current['Foreground']['old-session']['TCP']['8443']['HTTPS']=False
        self.assertNotEqual(other_routes(old),other_routes(current))
    def test_bad_or_encoded_paths_cannot_expand_allowlist(self):
        self.assertIsNone(resource_name("/icons/../index.html"))
        self.assertIsNone(resource_name("/icons%5cicon.svg"))
        self.assertEqual(resource_name("/"), "index.html")

    def test_occupied_port_in_nested_foreground_config_is_detected(self):
        self.assertFalse(port_in_use({"TCP": {"443": {"HTTPS": True}}}))
        self.assertTrue(port_in_use({"TCP": {"10001": {"HTTPS": True}}}))
        self.assertTrue(port_in_use({"Foreground": {"fictional-session": {"Web": {"goose.example.ts.net:10001": {}}}}}))
        self.assertTrue(port_in_use({"AllowFunnel": {"goose.example.ts.net:10001": True}}))

    def test_only_expected_https_loopback_proxy_is_accepted(self):
        route = {"TCP": {"10001": {"HTTPS": True}}, "Web": {"goose.example.ts.net:10001": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9394"}}}}}
        self.assertTrue(route_is_ours(route, "goose.example.ts.net"))
        self.assertTrue(route_is_ours({"Foreground": {"fictional": route}}, "goose.example.ts.net"))
        self.assertFalse(route_is_ours(route, "different.example.ts.net"))
        route["AllowFunnel"] = {"goose.example.ts.net:10001": True}
        self.assertFalse(route_is_ours(route, "goose.example.ts.net"))
        route.pop("AllowFunnel")
        route["Web"]["goose.example.ts.net:10001"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:9999"
        self.assertFalse(route_is_ours(route, "goose.example.ts.net"))

    def test_dns_comes_only_from_own_running_status(self):
        self.assertEqual(own_dns_name({"BackendState": "Running", "Self": {"DNSName": "goose.example.ts.net."}}), "goose.example.ts.net")
        for status in ({}, {"BackendState": "Stopped"}, {"BackendState": "Running", "Self": {"DNSName": "https://bad.invalid/"}}):
            with self.assertRaises(LauncherError):
                own_dns_name(status)

    def test_status_failure_does_not_expose_command_output(self):
        class Result:
            returncode = 1
            stdout = b"FICTIONAL_ACCOUNT_AND_IP"
        with patch("launch.Path.is_file", return_value=True), patch("launch.subprocess.run", return_value=Result()):
            with self.assertRaises(LauncherError) as failure:
                tailscale_json("status", "--json")
        self.assertNotIn("FICTIONAL_ACCOUNT", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
