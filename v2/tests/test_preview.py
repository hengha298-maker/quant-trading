"""Read-only preview server checks. No Internet or account data is accessed."""

from http.client import HTTPConnection
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "preview.py"
SPEC = importlib.util.spec_from_file_location("goose_v2_preview", MODULE_PATH)
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.original_root = preview.PUBLIC_ROOT
        preview.PUBLIC_ROOT = Path(cls.directory.name) / "demo"
        preview.PUBLIC_ROOT.mkdir()
        # Match production PUBLIC_ROOT (__file__.resolve().parent). Windows
        # temporary roots may contain an 8.3 alias such as RUNNER~1; resolve that
        # existing root before comparing each asset's canonical path. The asset
        # symlink rejection in PreviewHandler and its regression test stay intact.
        preview.PUBLIC_ROOT = preview.PUBLIC_ROOT.resolve(strict=True)
        for name in preview.PUBLIC_FILES:
            target = preview.PUBLIC_ROOT / name
            target.parent.mkdir(exist_ok=True, parents=True)
            target.write_bytes(("fictional demo: " + name).encode("utf-8"))
        (preview.PUBLIC_ROOT / "README.md").write_text("not public", encoding="utf-8")
        cls.server = preview.create_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        preview.PUBLIC_ROOT = cls.original_root
        cls.directory.cleanup()

    def request(self, method="GET", path="/", body=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_default_binding_is_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_root_and_all_public_assets_are_served(self):
        for path in ["/"] + ["/" + name for name in preview.PUBLIC_FILES]:
            with self.subTest(path=path):
                status, headers, content = self.request(path=path)
                self.assertEqual(status, 200)
                self.assertTrue(content.startswith(b"fictional demo:"))
                self.assertEqual(int(headers["Content-Length"]), len(content))

    def test_head_has_headers_without_body(self):
        status, headers, content = self.request("HEAD", "/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(content, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))

    def test_private_paths_and_traversal_are_not_served(self):
        for path in [
            "/README.md", "/preview.py", "/tests/test_preview.py", "/assets/", "/.git/config",
            "/../config.json", "/%2e%2e/config.json", "/assets/../README.md",
            "/%69ndex.html", "/data/paper.sqlite3", "/bark.local.json", "/api/holdings",
            "/app.js?secret=not-logged", "http://example.invalid/index.html", "//example.invalid/",
        ]:
            with self.subTest(path=path):
                status, _, content = self.request(path=path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"not public", content)
                self.assertNotIn(path.encode("utf-8"), content)

    def test_read_only_methods(self):
        for method in ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"]:
            with self.subTest(method=method):
                status, headers, _ = self.request(method, "/", body="fictional input")
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], "GET, HEAD")
        self.assertFalse((preview.PUBLIC_ROOT / "upload").exists())

    def test_security_and_cache_headers(self):
        status, headers, _ = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("form-action 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_error_and_head_error_do_not_disclose_paths(self):
        status, headers, content = self.request("HEAD", "/missing")
        self.assertEqual(status, 404)
        self.assertEqual(content, b"")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_asset_symlink_cannot_expose_unlisted_or_outside_file(self):
        target = preview.PUBLIC_ROOT / "assets/192.png"
        original = target.read_bytes()
        target.unlink()
        try:
            try:
                target.symlink_to(preview.PUBLIC_ROOT / "README.md")
            except OSError:
                self.skipTest("Creating symlinks is unavailable on this system")
            self.assertEqual(self.request(path="/assets/192.png")[0], 404)
            target.unlink()
            target.symlink_to(Path(self.directory.name) / "outside.txt")
            (Path(self.directory.name) / "outside.txt").write_text("outside", encoding="utf-8")
            self.assertEqual(self.request(path="/assets/192.png")[0], 404)
        finally:
            target.unlink(missing_ok=True)
            target.write_bytes(original)

    def test_port_validation(self):
        self.assertEqual(preview.valid_port("8765"), 8765)
        for value in ["0", "65536", "abc"]:
            with self.subTest(value=value), self.assertRaises(preview.argparse.ArgumentTypeError):
                preview.valid_port(value)


if __name__ == "__main__":
    unittest.main()
