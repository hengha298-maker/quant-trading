"""Authenticated read-only bridge; private input requires a TailnetGate.

The original loopback harness and the separately supervised Tailscale transport
share authentication and fixed assets. Private data has a separate constructor.
"""
import argparse
from copy import deepcopy
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import re
import sys
import threading
import time
import webbrowser

from p1_dependency import load_p1

REAL_DATA_ENABLED = False
ROOT = Path(__file__).resolve().parent
ASSETS = {'/readonly/': ('readonly.html', 'text/html; charset=utf-8'),
    '/readonly.js': ('readonly.js', 'text/javascript; charset=utf-8'),
    '/readonly-model.js': ('readonly-model.js', 'text/javascript; charset=utf-8'),
    '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
    '/readonly.css': ('readonly.css', 'text/css; charset=utf-8'),
    '/assets/icon.svg': ('assets/icon.svg', 'image/svg+xml')}


class Access:
    def __init__(self, view, *, clock=time.monotonic):
        self.lock = threading.RLock()
        self.clock = clock
        self.view = view
        self.closed = False
        self.session = None
        self.expires = 0
        self.binding = None
        self.rotate()

    def rotate(self):
        with self.lock:
            if self.closed:
                raise ValueError('closed')
            self.session = None
            self.binding = None
            self.pair_code = f'{secrets.randbelow(100_000_000):08d}'
            self.pair_expires = self.clock()+300
            self.failures = 0
            return self.pair_code

    def pair(self, code, binding=None):
        with self.lock:
            if self.closed or self.pair_code is None or self.clock() >= self.pair_expires or self.failures >= 5:
                return None
            if not isinstance(code, str) or not re.fullmatch(r'[0-9]{8}',code) or not hmac.compare_digest(code, self.pair_code):
                self.failures += 1
                return None
            self.pair_code = None
            self.session = secrets.token_urlsafe(32)
            self.binding = binding
            self.expires = self.clock()+900
            return self.session

    def authorized(self, token, binding=None):
        with self.lock:
            return bool(not self.closed and self.binding == binding and self.session and self.clock()<self.expires and
                isinstance(token,str) and re.fullmatch(r'[A-Za-z0-9_-]{43}',token) and hmac.compare_digest(token,self.session))

    def revoke(self):
        with self.lock:
            self.session = None
            self.binding = None
            self.pair_code = None

    def close(self):
        with self.lock:
            self.revoke()
            self.closed = True
            self.view = None


class Handler(BaseHTTPRequestHandler):
    server_version = 'GooseFixture/1'
    sys_version = ''
    protocol_version = 'HTTP/1.0'
    def setup(self):
        super().setup()
        self.connection.settimeout(5)
    def log_message(self, *_args):
        pass  # No paths, cookies, codes, request bodies or account data in logs.
    def _trace(self, code):
        transport = self.server.transport
        if transport is not None and hasattr(transport, 'trace'):
            transport.trace.record(getattr(self, 'trace_ticket', None), code)
    def _send(self, status, body, mime='application/json; charset=utf-8', cookie=None):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
        self.send_response(status)
        for key,value in [('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store, max-age=0'),
            ('Pragma','no-cache'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
            ('X-Frame-Options','DENY'),('X-Goose-Test-Only','false' if self.server.real_data else 'true'),
            ('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; worker-src 'none'; manifest-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]:
            self.send_header(key,value)
        if cookie:
            self.send_header('Set-Cookie',cookie)
        if self.server.secure_transport:
            self.send_header('Strict-Transport-Security','max-age=86400')
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except OSError:
                self._trace('phone_reply_failed')
                raise
            if status == 200 and self.path == '/readonly/' and getattr(self, 'device_binding', None):
                self._trace('phone_page_sent')
    def _guard(self, post=False):
        self.device_binding = None
        if self.server.real_data and (not self.server.secure_transport or self.server.transport is None):
            self._send(403,dict(error='secure_transport_required')); return False
        if self.server.transport is not None:
            try:
                self.device_binding = self.server.transport.verify(self)
            except (ValueError, OSError):
                self._send(403,dict(error='device_denied'));return False
        hosts = self.headers.get_all('Host', [])
        origin = self.headers.get_all('Origin', [])
        if hosts != [self.server.authority] or self.path not in set(ASSETS)|{'/api/v1/view','/session/pair','/session/logout'}:
            self._trace('phone_host_route_rejected')
            self._send(404,dict(error='not_found')); return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and origin != [self.server.origin]) or (post and origin != [self.server.origin]):
            self._trace('phone_origin_rejected')
            self._send(403,dict(error='origin_denied')); return False
        return True
    def _token(self):
        values = self.headers.get_all('Cookie', [])
        if len(values) != 1 or len(values[0])>2048:
            return None
        try:
            if sum(part.strip().startswith(self.server.cookie_name+'=') for part in values[0].split(';')) != 1:return None
            cookie = SimpleCookie();cookie.load(values[0])
            item = cookie.get(self.server.cookie_name)
            return item.value if item else None
        except Exception:
            return None
    def do_GET(self):
        if not self._guard(): return
        if self.path == '/api/v1/view':
            with self.server.access.lock:
                if not self.server.access.authorized(self._token(),self.device_binding):
                    self._send(401,dict(error='unauthorized'));return
                self._send(200,deepcopy(self.server.access.view))
            return
        if self.path not in ASSETS:
            self._send(405,dict(error='method_not_allowed'));return
        name,mime = ASSETS[self.path]
        path = ROOT / name
        try:
            if path.resolve(strict=True) != path.absolute() or not path.is_file():
                raise ValueError()
            content = path.read_bytes()
            if name == 'readonly.html' and self.server.real_data:
                content = content.replace(b'data-private="false"', b'data-private="true"')
            self._send(200,content,mime)
        except (ValueError,OSError):
            self._send(404,dict(error='not_found'))
    do_HEAD = do_GET
    def do_POST(self):
        if not self._guard(post=True): return
        if self.path not in ('/session/pair','/session/logout'):
            self._send(405,dict(error='read_only'));return
        lengths = self.headers.get_all('Content-Length', [])
        if (len(lengths)!=1 or len(lengths[0])>4 or not lengths[0].isascii() or not lengths[0].isdigit() or
            not 0<int(lengths[0])<=1024 or self.headers.get('Transfer-Encoding') is not None or
            self.headers.get('Content-Type') != 'application/json'):
            self._send(400,dict(error='invalid_request'));return
        try:
            def unique(pairs):
                out={}
                for k,v in pairs:
                    if k in out: raise ValueError()
                    out[k]=v
                return out
            raw=self.rfile.read(int(lengths[0]))
            if len(raw)!=int(lengths[0]):raise ValueError()
            body=json.loads(raw,object_pairs_hook=unique)
            if not isinstance(body,dict): raise ValueError()
        except (ValueError,UnicodeError,TimeoutError,OSError):
            self._send(400,dict(error='invalid_request'));return
        if self.path == '/session/pair':
            token = self.server.access.pair(body.get('code'),self.device_binding) if set(body)=={'code'} else None
            if token is None:
                self._send(401,dict(error='pair_denied'));return
            secure='; Secure' if self.server.secure_transport else ''
            self._send(200,dict(status='paired',test_data=not self.server.real_data),cookie=f'{self.server.cookie_name}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=900{secure}')
        else:
            if body or not self.server.access.authorized(self._token(),self.device_binding):
                self._send(401,dict(error='unauthorized'));return
            self.server.access.revoke()
            secure='; Secure' if self.server.secure_transport else ''
            self._send(200,dict(status='revoked'),cookie=f'{self.server.cookie_name}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{secure}')
    def _deny(self):
        if self._guard(): self._send(405,dict(error='read_only'))
    do_PUT=do_PATCH=do_DELETE=do_OPTIONS=do_TRACE=do_CONNECT=_deny


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    def handle_error(self, request, client_address):
        pass  # Sanitized failures only; never dump request details or traceback.
    def server_close(self):
        access=getattr(self,'access',None)
        if access is not None:access.close()
        super().server_close()


def create_server(p1_code, *, port=9876, scenario='normal'):
    if REAL_DATA_ENABLED is not False:
        raise ValueError('Private files are forbidden on fixture HTTP; use the private HTTPS launcher')
    from readonly_fixture import build_fixture_view
    p1=load_p1(p1_code)
    view=build_fixture_view(p1,scenario)
    if view['test_data'] is not True or view['mode']!='real_readonly':
        raise ValueError('Fixture gate failed')
    if len(json.dumps(view,ensure_ascii=True).encode('ascii'))>128000:
        raise ValueError('Fixture projection too large')
    server=FixtureServer(('127.0.0.1',port),Handler)
    server.access=Access(view)
    server.real_data=False
    server.transport=None
    server.secure_transport=False
    server.authority=f'127.0.0.1:{server.server_port}'
    server.origin='http://'+server.authority
    server.cookie_name='goose_v2_fixture_'+str(server.server_port)
    return server


def create_private_server(p1_code, snapshot_file, market_files, *, gate, authority):
    # A caller cannot expose private selections using the loopback fixture API.
    from tailscale_transport import TailnetGate, HTTPS_PORT
    from readonly_private import build_private_view
    if type(gate) is not TailnetGate or gate.ready or not re.fullmatch(
            r'[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*\.ts\.net:' + str(HTTPS_PORT), authority):
        raise ValueError('真实模式必须由指定设备私网HTTPS启动器建立。')
    view = build_private_view(load_p1(p1_code), snapshot_file, market_files)
    server = FixtureServer(('127.0.0.1', 0), Handler)
    server.access = Access(view)
    server.real_data = True
    server.transport = gate
    server.secure_transport = True
    server.authority = authority
    server.origin = 'https://' + authority
    server.cookie_name = '__Host-goose_v2_private'
    return server


def main(argv=None):
    from readonly_fixture import SCENARIOS
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description='仅虚构数据、本机、需配对的real_readonly开发测试；不支持真实持仓或LAN')
    parser.add_argument('--p1-code',required=True,help='指定版本PR #3源码目录或其中p1目录，绝非数据目录')
    def port_number(value):
        n=int(value)
        if not 1<=n<=65535:raise argparse.ArgumentTypeError('端口须为1至65535')
        return n
    parser.add_argument('--port',type=port_number,default=9876)
    parser.add_argument('--scenario',choices=SCENARIOS,default='normal')
    parser.add_argument('--no-open',action='store_true')
    args=parser.parse_args(argv)
    try: server=create_server(args.p1_code,port=args.port,scenario=args.scenario)
    except (ValueError,OSError):
        print('无法启动：P1核心源码版本不符或端口不可用；不读取持仓、不自动安装依赖。');return 1
    print('仅虚构测试，真实持仓入口关闭；不支持手机或局域网连接。')
    print(server.origin+'/readonly/')
    print('一次性测试配对码（5分钟有效）：'+server.access.pair_code,flush=True)
    if not args.no_open:webbrowser.open(server.origin+'/readonly/')
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
    return 0


if __name__=='__main__':raise SystemExit(main())
