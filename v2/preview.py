"""Local, read-only preview of the public V2 demo. Python standard library only.

This server does not serve the repository or accept uploads. It intentionally
knows nothing about V1, P1, account files, credentials, or cloud services.
"""

from __future__ import annotations

import argparse
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit
import webbrowser


PUBLIC_ROOT = Path(__file__).resolve().parent
PUBLIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "model.js": "text/javascript; charset=utf-8",
    "demo-data.js": "text/javascript; charset=utf-8",
    "manifest.webmanifest": "application/manifest+json; charset=utf-8",
    "sw.js": "text/javascript; charset=utf-8",
    "assets/icon.svg": "image/svg+xml",
    "assets/192.png": "image/png",
    "assets/512.png": "image/png",
    "assets/apple-touch-icon.png": "image/png",
}


class PreviewHandler(BaseHTTPRequestHandler):
    """Serve only an explicit list of non-sensitive demo assets."""

    server_version = "GooseDemo/1"
    sys_version = ""

    def log_message(self, _format, *args):
        # Do not echo arbitrary request paths, query strings, or client details.
        pass

    def _headers(self, status, content_type, length):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        )
        self.end_headers()

    def _not_found(self, head_only=False):
        content = "此预览仅提供养鹅计划 V2 的公开演示资源。".encode("utf-8")
        self._headers(404, "text/plain; charset=utf-8", len(content))
        if not head_only:
            self.wfile.write(content)

    def _serve(self, head_only=False):
        try:
            request = urlsplit(self.path)
        except ValueError:
            self._not_found(head_only)
            return
        # Exact paths only. Encoded paths, traversal, queries, alternate roots,
        # directories, documentation, tests, and private files are not exposed.
        if request.scheme or request.netloc or request.query or request.fragment:
            self._not_found(head_only)
            return
        name = "index.html" if request.path == "/" else request.path.removeprefix("/")
        if request.path != "/" and request.path != "/" + name:
            self._not_found(head_only)
            return
        if name not in PUBLIC_FILES:
            self._not_found(head_only)
            return
        try:
            candidate = PUBLIC_ROOT / name
            # A replaced asset symlink must not expose an outside file.
            resolved = candidate.resolve(strict=True)
            if resolved != candidate.absolute() or not resolved.is_relative_to(PUBLIC_ROOT) or not resolved.is_file():
                self._not_found(head_only)
                return
            content = resolved.read_bytes()
        except (OSError, RuntimeError):
            self._not_found(head_only)
            return
        self._headers(200, PUBLIC_FILES[name], len(content))
        if not head_only:
            self.wfile.write(content)

    def do_GET(self):
        self._serve()

    def do_HEAD(self):
        self._serve(head_only=True)

    def _method_not_allowed(self):
        content = "此演示为只读，不接受上传或修改。".encode("utf-8")
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_TRACE = _method_not_allowed
    do_CONNECT = _method_not_allowed


def create_server(host="127.0.0.1", port=8765):
    """Create a local server; callers own serve_forever/shutdown/server_close."""
    server = ThreadingHTTPServer((host, port), PreviewHandler)
    server.daemon_threads = True
    return server


def local_ipv4_addresses():
    """Best-effort host lookup, without connecting to an Internet destination."""
    try:
        addresses = {
            entry[4][0]
            for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        }
    except OSError:
        return []
    return sorted(
        address
        for address in addresses
        if ipaddress.ip_address(address).is_private
        and not ipaddress.ip_address(address).is_loopback
        and not ipaddress.ip_address(address).is_unspecified
    )


def valid_port(value):
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口须为 1 至 65535 的整数。") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口须为 1 至 65535 的整数。")
    return port


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="养鹅计划 V2：仅虚构数据的本地只读预览")
    parser.add_argument("--port", type=valid_port, default=8765)
    parser.add_argument("--lan", action="store_true", help="显式允许同一局域网中的设备查看演示")
    parser.add_argument("--no-open", action="store_true", help="不自动打开本机浏览器")
    args = parser.parse_args(argv)
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        server = create_server(host, args.port)
    except OSError:
        print("无法启动预览：端口可能被占用。关闭旧预览窗口后重试，或用 --port 选择其他端口。")
        return 1
    local_url = f"http://127.0.0.1:{server.server_port}/"
    print("养鹅计划 V2 · 只读演示")
    print("全部为虚构演示数据，不是真实行情、真实账户或投资建议。")
    print("不安装依赖，不读取 V1/P1，不发送 Bark，不连接云端或券商。")
    print(f"电脑预览：{local_url}")
    if args.lan:
        print("已显式开启同网预览：同一 Wi-Fi 内的设备可查看公开的演示数据。")
        for address in local_ipv4_addresses():
            print(f"手机 Safari 打开：http://{address}:{server.server_port}/")
        print("若未显示可用地址，在 Windows 网络设置查看本机 IPv4；不要设置公网端口映射。")
        print("可能需要为本次预览允许 Windows 防火墙的专用网络访问；不需要关闭防火墙。")
        print("手机局域网 HTTP 可体验网页；完整离线 PWA 需要 HTTPS，本期未部署 HTTPS 服务。")
    else:
        print("仅本机可访问。手机同网预览请双击“手机同网预览.cmd”。")
    print("关闭本窗口或按 Ctrl+C 即停止。不会新增后台服务或计划任务。", flush=True)
    if not args.no_open:
        webbrowser.open(local_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n预览已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
