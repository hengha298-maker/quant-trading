"""Read-only, loopback-only delivery of the Goose V3 application shell.

The server has no financial-data endpoints and never receives backup files.
Only the resources named below can be served; it is not a directory server.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

HOST = "127.0.0.1"
PORT = 9394
WEB_ROOT = Path(__file__).resolve().parent / "web"
RESOURCES = {
    "research-report-v32.json": "application/json; charset=utf-8",
    "research-report-v31.json": "application/json; charset=utf-8",
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "domain.js": "text/javascript; charset=utf-8",
    "io.js": "text/javascript; charset=utf-8",
    "planning.js": "text/javascript; charset=utf-8",
    "investment-ui.js": "text/javascript; charset=utf-8",
    "sw.js": "text/javascript; charset=utf-8",
    "manifest.webmanifest": "application/manifest+json; charset=utf-8",
    "icons/icon.svg": "image/svg+xml",
    "icons/icon-192.png": "image/png",
    "icons/icon-512.png": "image/png",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self'; font-src 'self'; connect-src 'self'; worker-src 'self'; "
    "manifest-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-src 'none'; frame-ancestors 'none'"
)


def resource_name(target: str) -> str | None:
    """Return one exact allowlisted name, rejecting ambiguous HTTP targets."""
    if not target.startswith("/") or target.startswith("//"):
        return None
    if re.search(r"%(?![0-9a-fA-F]{2})", target):
        return None
    try:
        parsed = urlsplit(target)
        path = unquote(parsed.path, errors="strict")
    except (ValueError, UnicodeError):
        return None
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return None
    if "\\" in path or "\x00" in path or path.startswith("//"):
        return None
    name = "index.html" if path == "/" else path[1:]
    return name if name in RESOURCES else None


class StaticServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port: int = PORT, web_root: Path = WEB_ROOT):
        self.web_root = Path(web_root).absolute()
        if self.web_root.is_symlink() or self.web_root.is_junction() or not self.web_root.is_dir():
            raise ValueError("V3_WEB_ROOT_INVALID")
        super().__init__((HOST, port), StaticHandler)


class StaticHandler(BaseHTTPRequestHandler):
    server: StaticServer
    server_version = "GooseV3"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # URLs, cookies and request headers must never enter an access log.
        return

    def __getattr__(self, name: str):
        if name.startswith("do_"):
            return self._deny_method
        raise AttributeError(name)

    def version_string(self) -> str:
        return "GooseV3"

    def _headers(self, status: int, length: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cache-Control", "no-cache, max-age=0, must-revalidate")
        self.end_headers()

    def _error(self, status: HTTPStatus) -> None:
        # Do not reflect request paths, exception messages or local file names.
        body = (str(int(status)) + "\n").encode("ascii")
        self.close_connection = True
        self._headers(status, len(body), "text/plain; charset=utf-8")
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve(self, *, head: bool = False) -> None:
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
            self._error(HTTPStatus.BAD_REQUEST)
            return
        name = resource_name(self.path)
        if name is None:
            self._error(HTTPStatus.NOT_FOUND)
            return
        path = self.server.web_root / name
        try:
            # Reject symbolic links/junctions in every resource component.
            current = path
            while current != self.server.web_root:
                if current.is_symlink() or current.is_junction():
                    self._error(HTTPStatus.NOT_FOUND)
                    return
                current = current.parent
            path.resolve().relative_to(self.server.web_root.resolve())
            if not path.is_file():
                self._error(HTTPStatus.NOT_FOUND)
                return
            body = path.read_bytes()
        except (OSError, ValueError):
            self._error(HTTPStatus.NOT_FOUND)
            return
        self._headers(HTTPStatus.OK, len(body), RESOURCES[name])
        if not head:
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._serve()

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def _deny_method(self) -> None:
        self._error(HTTPStatus.METHOD_NOT_ALLOWED)

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _deny_method


def make_server(*, port: int = PORT, web_root: Path = WEB_ROOT) -> StaticServer:
    return StaticServer(port=port, web_root=web_root)


def main() -> None:
    with make_server() as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
