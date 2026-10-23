"""DEVELOPER TEST ONLY: local TLS proxy surrogate, NOT Tailscale Serve.

Only imports fictional peers and fixture provider. Certificates are ephemeral
test files, never production trust/key material. Never used by launchers.
"""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import ssl
import sys
import threading

from test_phone import AUTHORITY, DEVICE, FakeCLI, P1_CODE, bridge, phone


def main():
    backend = bridge.create_server(P1_CODE, port=0)
    cli = FakeCLI(); gate = phone.TailnetGate(cli, DEVICE); gate.ready = True
    backend.transport = gate; backend.secure_transport = True
    backend.authority = AUTHORITY; backend.origin = 'https://' + AUTHORITY
    backend.cookie_name = '__Host-goose_v2_fixture'
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def handle_request(self):
            connection = HTTPConnection('127.0.0.1', backend.server_port, timeout=5)
            try:
                headers = dict(self.headers)
                # Same overwrite semantics as the reviewed Serve source.
                for k in list(headers):
                    if k.lower().startswith(('x-forwarded-', 'tailscale-')): del headers[k]
                headers.update({'X-Forwarded-Host': AUTHORITY, 'X-Forwarded-Proto': 'https',
                    'X-Forwarded-For': DEVICE.ips[0], 'Tailscale-User-Login': DEVICE.login})
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                connection.request(self.command, gate.prefix + self.path, body, headers)
                response = connection.getresponse(); content = response.read()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in ('server', 'date', 'connection'): self.send_header(key, value)
                self.end_headers()
                if self.command != 'HEAD': self.wfile.write(content)
            except OSError:
                self.send_error(503)
            finally: connection.close()
        do_GET = do_POST = do_HEAD = handle_request
    proxy = ThreadingHTTPServer(('127.0.0.1', 8443), Proxy)
    proxy.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(os.environ['GOOSE_TEST_TLS_CERT'], os.environ['GOOSE_TEST_TLS_KEY'])
    proxy.socket = context.wrap_socket(proxy.socket, server_side=True)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (backend, proxy)]
    for thread in threads: thread.start()
    print(json.dumps({'origin': backend.origin, 'code': backend.access.pair_code}), flush=True)
    try:
        for line in sys.stdin:
            command = json.loads(line)['op']
            if command == 'rotate': result = {'code': backend.access.rotate()}
            elif command == 'expire': backend.access.expires = 0; result = {'ok': True}
            elif command == 'deny': cli.who['Node']['StableID'] = 'nOther'; result = {'ok': True}
            elif command == 'stop': break
            else: raise ValueError('unsupported fixture operation')
            print(json.dumps(result), flush=True)
    finally:
        gate.ready = False; backend.access.close()
        for server in (proxy, backend): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=3)
    return 0


if __name__ == '__main__': raise SystemExit(main())
