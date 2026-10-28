"""Private, bounded local report viewer. No broker access or notification sender."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import urlsplit

MAX_BODY = 8 * 1024 * 1024
MAX_UPLOAD = 5 * 1024 * 1024
MAX_REPORT = 10 * 1024 * 1024
PREFIX = '/investment/api/'
WEB = Path(__file__).parent / 'web'


def _plain(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _credential_matches(received, expected):
    return secrets.compare_digest(received.encode('utf-8'), expected.encode('utf-8'))


def _contained_file(path, root):
    """Reject directory traversal and links, including reparse-point ancestors."""
    root = Path(root).absolute()
    try:
        for ancestor in [root, *root.parents]:
            if ancestor.is_symlink() or getattr(ancestor.lstat(), 'st_file_attributes', 0) & 0x400:
                return None
    except OSError:
        return None
    root = root.resolve()
    path = Path(path)
    try:
        path.relative_to(root)
        cursor = path
        while cursor != root:
            stat = cursor.lstat()
            if cursor.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
                return None
            cursor = cursor.parent
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        return resolved if resolved.is_file() else None
    except (ValueError, OSError):
        return None


def _reports(repo):
    runs = Path(repo) / 'investment' / 'work' / 'runs'
    if not runs.exists():
        return []
    records = []
    for path in runs.glob('*/report.json'):
        safe = _contained_file(path, runs)
        if not safe or safe.stat().st_size > MAX_REPORT:
            continue
        try:
            report = json.loads(safe.read_text(encoding='utf-8'))
            if type(report) is dict and isinstance(report.get('id'), str):
                records.append((str(report.get('generatedAt', '')), safe, report))
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda row: row[0], reverse=True)


def make_server(root, port=9395, host='127.0.0.1', analyze=None, *, allowed_hosts=None, allowed_origins=None, access_key=None):
    """Return a server; caller starts serve_forever. Only loopback binding is allowed.

    An existing, separately configured private HTTPS proxy may supply an explicit
    Host/Origin allowlist. Forwarded headers never change the security decision.
    """
    if host not in {'127.0.0.1', '::1'}:
        raise ValueError('LOOPBACK_BIND_REQUIRED')
    original_root = Path(root).absolute()
    for ancestor in [original_root, *original_root.parents]:
        if ancestor.is_symlink() or getattr(ancestor.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('REPARSE_WORKSPACE_REJECTED')
    repo = original_root.resolve(strict=True)
    state_lock = threading.Lock()
    state = {'running': False, 'startedAt': None, 'finishedAt': None, 'error': None, 'reportId': None}
    csrf = secrets.token_urlsafe(32)
    access_key = access_key or secrets.token_urlsafe(32)
    if not isinstance(access_key, str) or len(access_key) < 32:
        raise ValueError('STRONG_ACCESS_KEY_REQUIRED')
    previews = {}
    hosts = set(allowed_hosts or ())
    origins = set(allowed_origins or ())

    class Handler(BaseHTTPRequestHandler):
        server_version = 'GoosePrivate'
        sys_version = ''

        def log_message(self, format, *args):
            # Avoid writing uploaded filenames, query strings, tokens or holdings.
            pass

        def _headers(self, status, data, content_type='application/json; charset=utf-8', download=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="' + download + '"')
            self.end_headers()
            if self.command != 'HEAD':
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _json(self, status, value):
            self._headers(status, _plain(value))

        def _import_error(self, error, status=400, default='IMPORT_INVALID'):
            from .holdings import HoldingsError
            value = {'error': getattr(error, 'code', default)}
            if isinstance(error, HoldingsError):
                value['message'] = str(error)
            self._json(status, value)

        def _preview_response(self, preview):
            preview_id = secrets.token_urlsafe(24)
            with state_lock:
                now = time.monotonic()
                for key, item in list(previews.items()):
                    if now - item[0] > 900:
                        del previews[key]
                if len(previews) >= 20:
                    self._json(409, {'error': 'TOO_MANY_PENDING_PREVIEWS'})
                    return
                previews[preview_id] = (now, preview)
            self._json(200, {'previewId': preview_id, 'preview': preview})

        def _guard(self, mutation=False):
            if len(self.headers.get_all('Host', [])) != 1 or self.headers.get('Host', '').lower() not in hosts:
                self._json(403, {'error': 'HOST_NOT_ALLOWED'})
                return False
            origin = self.headers.get('Origin')
            if len(self.headers.get_all('Origin', [])) > 1 or origin and origin not in origins:
                self._json(403, {'error': 'ORIGIN_NOT_ALLOWED'})
                return False
            if mutation and (origin not in origins or not _credential_matches(self.headers.get('X-Goose-CSRF', ''), csrf)):
                self._json(403, {'error': 'CSRF_REQUIRED'})
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self._json(403, {'error': 'CROSS_SITE_DENIED'})
                return False
            if urlsplit(self.path).path.startswith(PREFIX):
                authorization = self.headers.get('Authorization', '')
                if not _credential_matches(authorization, 'Bearer ' + access_key):
                    self._json(401, {'error': 'PRIVATE_ACCESS_REQUIRED'})
                    return False
            return True

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._guard():
                return
            request = urlsplit(self.path)
            if request.scheme or request.netloc or request.query or request.fragment or '%' in request.path or '\\' in request.path:
                self._json(400, {'error': 'INVALID_PATH'})
                return
            route = request.path
            if route == PREFIX + 'session':
                self._json(200, {'csrf': csrf})
            elif route == PREFIX + 'status':
                with state_lock:
                    self._json(200, dict(state))
            elif route == PREFIX + 'latest':
                from .engine import mark_report_freshness
                reports = _reports(repo)
                self._json(200, {'report': mark_report_freshness(reports[0][2]) if reports else None})
            elif route == PREFIX + 'history':
                self._json(200, {'reports': [{'id': item[2]['id'], 'generatedAt': item[2].get('generatedAt'), 'asOf': item[2].get('asOf'), 'status': item[2].get('status'), 'summary': item[2].get('summary')} for item in _reports(repo)[:100]]})
            elif route.startswith(PREFIX + 'history/'):
                identifier = route[len(PREFIX + 'history/'):]
                if not identifier or len(identifier) > 100 or '/' in identifier:
                    self._json(404, {'error': 'REPORT_MISSING'})
                    return
                report = next((item[2] for item in _reports(repo) if item[2]['id'] == identifier), None)
                if report:
                    from .engine import mark_report_freshness
                    report = mark_report_freshness(report)
                self._json(200 if report else 404, {'report': report} if report else {'error': 'REPORT_MISSING'})
            elif route == PREFIX + 'holdings':
                try:
                    from .holdings import load_latest, load_latest_by_broker
                    location = repo / 'investment' / 'work' / 'private' / 'holdings'
                    current = load_latest(location)
                    self._json(200, {'holdings': current, 'accounts': load_latest_by_broker(location)})
                except (ValueError, OSError):
                    self._json(409, {'error': 'HOLDINGS_UNAVAILABLE'})
            elif route in {PREFIX + 'report-v31', PREFIX + 'report-json'}:
                reports = _reports(repo)
                if not reports:
                    self._json(404, {'error': 'REPORT_MISSING'})
                    return
                path = reports[0][1]
                if route.endswith('report-v31'):
                    path = _contained_file(path.parent / 'goose-report-v31.json', path.parent)
                if not path or not path.exists() or path.stat().st_size > MAX_REPORT:
                    self._json(404, {'error': 'PHONE_REPORT_MISSING'})
                    return
                self._headers(200, path.read_bytes(), download='goose-investment-report.json')
            else:
                # The existing PWA root is served unchanged, preserving its IndexedDB
                # and backup format. The new dashboard has no service worker.
                if route in {'/investment', '/investment/'}:
                    path, base = WEB / 'index.html', WEB
                elif route in {'/investment/app.js', '/investment/styles.css'}:
                    path, base = WEB / route.rsplit('/', 1)[-1], WEB
                else:
                    relative = 'index.html' if route == '/' else route.lstrip('/')
                    allowed = {'index.html', 'app.js', 'domain.js', 'investment-ui.js', 'io.js', 'planning.js', 'styles.css', 'sw.js', 'manifest.webmanifest', 'icons/icon.svg', 'icons/icon-192.png', 'icons/icon-512.png'}
                    if relative not in allowed:
                        self._json(404, {'error': 'NOT_FOUND'})
                        return
                    base = repo / 'v3' / 'web'
                    path = base / relative
                safe = _contained_file(path, base)
                if not safe:
                    self._json(404, {'error': 'NOT_FOUND'})
                    return
                kind = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json'}.get(path.suffix, 'application/octet-stream')
                self._headers(200, safe.read_bytes(), kind)

        def do_POST(self):
            if not self._guard(mutation=True):
                return
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json' or self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                self._json(415, {'error': 'JSON_REQUIRED'})
                return
            try:
                length = int(self.headers.get('Content-Length', ''))
                if not 0 < length <= MAX_BODY:
                    raise ValueError()
            except ValueError:
                self._json(413, {'error': 'BODY_LIMIT'})
                return
            self.connection.settimeout(20)
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                if type(body) is not dict:
                    raise ValueError()
            except (ValueError, OSError):
                self._json(400, {'error': 'INVALID_JSON'})
                return
            route = self.path
            if route == PREFIX + 'analyze':
                with state_lock:
                    if state['running']:
                        self._json(409, {'error': 'ANALYSIS_ALREADY_RUNNING'})
                        return
                    state.update(running=True, startedAt=datetime.now(timezone.utc).isoformat(), finishedAt=None, error=None, reportId=None)
                def job():
                    try:
                        if analyze is None:
                            from .engine import run_analysis
                            report = run_analysis(repo)
                        else:
                            report = analyze(repo)
                        with state_lock:
                            state['reportId'] = report.get('id')
                    except Exception:
                        with state_lock:
                            state['error'] = 'ANALYSIS_FAILED_SEE_LOCAL_DIAGNOSTIC'
                    finally:
                        with state_lock:
                            state.update(running=False, finishedAt=datetime.now(timezone.utc).isoformat())
                threading.Thread(target=job, name='goose-read-only-analysis', daemon=True).start()
                self._json(202, {'status': 'started'})
            elif route == PREFIX + 'preview':
                try:
                    from .holdings import parse_file
                    name = str(body.get('fileName', ''))
                    suffix = Path(name).suffix.lower()
                    if suffix not in {'.csv', '.xlsx'}:
                        raise ValueError('STRUCTURED_CSV_OR_XLSX_REQUIRED')
                    raw = base64.b64decode(body.get('content', ''), validate=True)
                    if not raw or len(raw) > MAX_UPLOAD:
                        raise ValueError('FILE_LIMIT')
                    as_of = body.get('asOf')
                    broker = body.get('broker', '用户导出')
                    if not isinstance(as_of, str) or len(as_of) > 40 or not isinstance(broker, str) or len(broker) > 100:
                        raise ValueError('DATE_OR_BROKER_INVALID')
                    private = repo / 'investment' / 'work' / 'private' / 'uploads'
                    for ancestor in [private, *private.parents]:
                        if ancestor.exists() and (ancestor.is_symlink() or getattr(ancestor.lstat(), 'st_file_attributes', 0) & 0x400):
                            raise ValueError('UPLOAD_PATH_INVALID')
                    private.mkdir(parents=True, exist_ok=True)
                    # Raw export exists only during parsing and is never served.
                    with tempfile.NamedTemporaryFile(dir=private, suffix=suffix, delete=False) as handle:
                        file_path = Path(handle.name)
                        handle.write(raw)
                    try:
                        preview = parse_file(file_path, as_of=as_of, broker=broker)
                    finally:
                        file_path.unlink(missing_ok=True)
                    self._preview_response(preview)
                except (ValueError, TypeError, OSError, binascii.Error) as exc:
                    code = getattr(exc, 'code', None) or (str(exc) if str(exc) in {'STRUCTURED_CSV_OR_XLSX_REQUIRED', 'FILE_LIMIT', 'DATE_OR_BROKER_INVALID', 'TOO_MANY_PENDING_PREVIEWS'} else 'IMPORT_INVALID')
                    self._import_error(exc, default=code)
            elif route == PREFIX + 'manual-preview':
                try:
                    from .holdings import parse_manual
                    broker = body.get('broker')
                    if broker not in {'华宝证券', '银河证券'}:
                        self._json(400, {'error': 'SELECT_HUABAO_OR_GALAXY'})
                        return
                    preview = parse_manual(body.get('rows'), as_of=body.get('asOf'), broker=broker, available_cash=body.get('availableCash'))
                    self._preview_response(preview)
                except (ValueError, TypeError, OSError) as exc:
                    self._import_error(exc, default='MANUAL_IMPORT_INVALID')
            elif route == PREFIX + 'confirm':
                if body.get('confirmed') is not True:
                    self._json(400, {'error': 'EXPLICIT_CONFIRMATION_REQUIRED'})
                    return
                if not isinstance(body.get('previewId'), str) or len(body['previewId']) > 100:
                    self._json(400, {'error': 'PREVIEW_ID_REQUIRED'})
                    return
                with state_lock:
                    pending = previews.get(body.get('previewId'))
                if not pending or time.monotonic() - pending[0] > 900:
                    self._json(409, {'error': 'PREVIEW_EXPIRED_REIMPORT'})
                    return
                try:
                    from .holdings import commit_preview
                    result = commit_preview(pending[1], repo / 'investment' / 'work' / 'private' / 'holdings', confirmed=True)
                    with state_lock:
                        previews.pop(body['previewId'], None)
                    self._json(200, {key: result[key] for key in ['status', 'sha256', 'snapshotId'] if key in result})
                except (ValueError, OSError) as exc:
                    self._import_error(exc, status=409, default='IMPORT_FAILED')
            else:
                self._json(404, {'error': 'NOT_FOUND'})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    actual_port = server.server_address[1]
    hosts.update({f'127.0.0.1:{actual_port}', f'localhost:{actual_port}'})
    origins.update({f'http://127.0.0.1:{actual_port}', f'http://localhost:{actual_port}'})
    server.goose_repo = repo
    server.access_key = access_key
    return server
