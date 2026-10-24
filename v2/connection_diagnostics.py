"""Owner-only, in-memory connection checks. Never fetch assets or sessions.

Self HTTPS results describe Windows -> itself, not the iPhone direction.
Request observations contain only fixed codes and are scoped to one window.
"""
from http.client import HTTPSConnection, HTTPException
import ipaddress
import queue
import socket
import ssl
import threading
import time

CONNECTION_CODES = frozenset('''check_not_running check_self_address_missing
check_local_dns_failed check_local_dns_mismatch check_local_tcp_failed
check_local_tls_failed check_local_http_failed check_local_https_ok
check_local_tls_ok check_whois_ok check_whois_failed
phone_request_not_observed phone_candidate_arrived phone_proxy_headers_ok
phone_proxy_headers_rejected phone_whois_started phone_whois_timeout
phone_whois_cli_failed phone_whois_json_failed phone_identity_rejected
phone_identity_verified phone_host_route_rejected phone_origin_rejected
phone_page_sent phone_reply_failed check_cancelled'''.split())


class RequestTrace:
    def __init__(self, clock=time.monotonic):
        self.clock = clock; self.lock = threading.Lock()
        self.epoch = 0; self.deadline = 0; self.codes = set()

    def begin(self):
        with self.lock:
            self.epoch += 1; self.codes = set(); self.deadline = self.clock() + 40
            return self.epoch

    def candidate(self):
        with self.lock:
            if self.clock() >= self.deadline:
                return None
            self.codes.add('phone_candidate_arrived')
            return self.epoch

    def record(self, ticket, code):
        if code not in CONNECTION_CODES or ticket is None:
            return
        with self.lock:
            if ticket == self.epoch and self.clock() < self.deadline:
                self.codes.add(code)

    def finish(self, ticket):
        with self.lock:
            if ticket != self.epoch:
                return ('check_cancelled',)
            self.deadline = 0
            return tuple(sorted(self.codes)) if self.codes else ('phone_request_not_observed',)

    def cancel(self):
        with self.lock:
            self.epoch += 1; self.deadline = 0; self.codes.clear()


def _resolve(host, port):
    """Bound waiting for system DNS without changing DNS/proxy/VPN settings."""
    result = queue.Queue(maxsize=1)
    def run():
        try: result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception: result.put(None)
    threading.Thread(target=run, daemon=True).start()
    try: return result.get(timeout=4)
    except queue.Empty: return None


def check_local_https(authority, self_ips):
    """HEAD only; no cookies/body/code. DNS must resolve exclusively to Self.

    Dial an approved Self IP while retaining the hostname for SNI and certificate
    verification. This checks the actual Serve hop, not a fake authorized peer.
    """
    if not self_ips:
        return ('check_self_address_missing',)
    host, port_text = authority.rsplit(':', 1); port = int(port_text)
    resolved = _resolve(host, port)
    if not resolved:
        return ('check_local_dns_failed',)
    try:
        addresses = {str(ipaddress.ip_address(row[4][0])) for row in resolved}
        approved = {str(ipaddress.ip_address(value)) for value in self_ips}
    except (ValueError, TypeError, IndexError):
        return ('check_local_dns_failed',)
    if not addresses or not addresses <= approved:
        return ('check_local_dns_mismatch',)
    target = min(addresses, key=lambda ip: (ipaddress.ip_address(ip).version, ip))
    connection = None; raw = None
    try:
        raw = socket.create_connection((target, port), timeout=4)
    except OSError:
        return ('check_local_tcp_failed',)
    try:
        context = ssl.create_default_context()  # No TLS bypass or custom CA.
        connection = HTTPSConnection(host, port, timeout=4, context=context)
        connection.sock = context.wrap_socket(raw, server_hostname=host)
        raw = None  # connection now owns the TLS socket.
    except (OSError, ssl.SSLError):
        if raw is not None: raw.close()
        if connection is not None: connection.close()
        return ('check_local_tls_failed',)
    try:
        connection.request('HEAD', '/readonly/', headers={'Host': authority})
        response = connection.getresponse()
        # The computer is not the approved iPhone: 403 is the expected result.
        # Read only status/headers, never the body or a sensitive API endpoint.
        if response.status == 403 and response.getheader('X-Goose-Test-Only') in ('true', 'false'):
            return ('check_local_https_ok',)
        return ('check_local_tls_ok', 'check_local_http_failed')
    except (OSError, HTTPException):
        return ('check_local_tls_ok', 'check_local_http_failed')
    finally:
        connection.close()
