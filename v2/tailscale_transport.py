"""Tailscale Serve supervisor. No Funnel, ACL writes or login.

Trust boundary: the local Windows account/OS and installed Tailscale daemon are
trusted. HTTP backend is loopback only, with a per-run private proxy prefix.
Serve overwrites X-Forwarded-* and Tailscale identity headers; whois revalidates
the exact approved stable node, user and IP on EVERY request (including assets).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time

from readonly_server import create_server, create_private_server
from connection_diagnostics import CONNECTION_CODES, RequestTrace, check_local_https

HTTPS_PORT = 8443

# Only these literals may cross the diagnostic boundary. Never stringify CLI
# output, an exception, a path, a URL, an identity, or a request into a diagnostic.
ERROR_CODES = frozenset('''transport_internal_error operation_failed
invalid_tail_ip invalid_node tailscale_not_installed tailscale_status_invalid
tailscale_version_unsupported tailscale_not_connected tailscale_self_offline
tailscale_self_key_expired magicdns_required personal_identity_required
serve_config_unknown serve_schema_unsupported proxy_header_denied transport_denied device_denied
identity_unavailable serve_supervision_failed already_running
device_selection_expired existing_serve_or_funnel snapshot_required
serve_start_failed https_setup_required https_start_failed serve_exited
serve_failed serve_config_missing serve_configuration_changed
serve_session_changed device_unavailable device_identity_changed
device_addresses_changed device_offline device_not_ios device_key_expired
device_tagged_or_shared tailnet_dns_changed not_running stop_unconfirmed
watch_internal_error'''.split()) | CONNECTION_CODES | frozenset(
    f'cli_{operation}_{reason}'
    for operation in ('version', 'status', 'config', 'whois', 'other')
    for reason in ('timeout', 'launch', 'exit', 'size', 'encoding', 'json'))


def diagnostic_code(value):
    return value if isinstance(value, str) and value in ERROR_CODES else 'transport_internal_error'


class TailnetError(ValueError):
    """Only fixed diagnostic codes; never echo CLI output, paths or identities."""
    def __init__(self, code):
        self.code = diagnostic_code(code)
        super().__init__(self.code)


def _fail(code):
    raise TailnetError(code) from None


def _not_expired(value):
    if value is None or value == '0001-01-01T00:00:00Z':
        return True  # Tailscale represents disabled expiry as null or zero time.
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')) > datetime.now(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return False


def tail_ip(value):
    try:
        ip = ipaddress.ip_address(value)
        if ip in ipaddress.ip_network('100.64.0.0/10') or ip in ipaddress.ip_network('fd7a:115c:a1e0::/48'):
            return str(ip)
    except ValueError:
        pass
    _fail('invalid_tail_ip')


def _stable_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        _fail('invalid_node')
    return value


@dataclass(frozen=True)
class Device:
    node_id: str
    user_id: int
    login: str
    name: str
    ips: tuple


def device_check_failure(selected, devices, cli=None):
    """Display names and address order are NOT credentials. Keep every address.

    Stable ID, owner, login, and the complete approved address set must match;
    eligibility (online/iOS/key/tags/sharing) was checked by inspect().
    """
    for current in devices:
        if current.node_id != selected.node_id:
            continue
        if current.user_id != selected.user_id or current.login != selected.login:
            return 'device_identity_changed'
        if set(current.ips) != set(selected.ips):
            return 'device_addresses_changed'
        return None
    return diagnostic_code(getattr(cli, 'device_failures', {}).get(selected.node_id, 'device_unavailable'))


class Tailscale:
    def __init__(self):
        if os.name == 'nt':
            # Do not execute a same-name file in the downloaded ZIP/current dir.
            path = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tailscale' / 'tailscale.exe'
            self.executable = str(path) if path.is_file() else None
        else:
            self.executable = shutil.which('tailscale')
        if not self.executable:
            _fail('tailscale_not_installed')

    def command(self, *args):
        return [self.executable, *args]

    def run(self, *args, json_result=True):
        operation = {'version': 'version', 'status': 'status', 'serve': 'config', 'whois': 'whois'}.get(args[0] if args else '', 'other')
        try:
            result = subprocess.run(self.command(*args), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired:
            _fail(f'cli_{operation}_timeout')
        except (OSError, subprocess.SubprocessError):
            _fail(f'cli_{operation}_launch')
        if result.returncode:
            _fail(f'cli_{operation}_exit')
        if len(result.stdout) > 2_000_000:
            _fail(f'cli_{operation}_size')
        try:
            text = result.stdout.decode('utf-8')
        except UnicodeError:
            _fail(f'cli_{operation}_encoding')
        if not json_result:
            return text
        try:
            def unique(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj: raise ValueError()
                    obj[key] = value
                return obj
            def reject_constant(_value):
                raise ValueError()
            return json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)
        except (ValueError, RecursionError):
            _fail(f'cli_{operation}_json')

    def inspect(self):
        try:
            return self._inspect()
        except (KeyError, IndexError, TypeError, AttributeError):
            _fail('tailscale_status_invalid')

    def _inspect(self):
        self.device_failures = {}
        version = self.run('version', json_result=False).splitlines()[0]
        match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version.strip())
        if not match or tuple(map(int, match.groups())) < (1, 90, 9):
            _fail('tailscale_version_unsupported')
        status = self.run('status', '--json')
        if not isinstance(status, dict):
            _fail('tailscale_status_invalid')
        if status.get('BackendState') != 'Running':
            _fail('tailscale_not_connected')
        own = status.get('Self', {})
        if not isinstance(own, dict) or type(own.get('Online')) is not bool:
            _fail('tailscale_status_invalid')
        if own['Online'] is not True:
            _fail('tailscale_self_offline')
        if own.get('Expired', False) is not False or not _not_expired(own.get('KeyExpiry')):
            _fail('tailscale_self_key_expired')
        dns = own.get('DNSName', '').removesuffix('.').lower()
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ts\.net', dns):
            _fail('magicdns_required')
        user_id = own.get('UserID')
        users = status.get('User') or {}
        login = users.get(str(user_id), {}).get('LoginName')
        if type(user_id) is not int or user_id <= 0 or not isinstance(login, str) or not login.isascii() or not login or len(login) > 254:
            _fail('personal_identity_required')
        devices = []
        for peer in (status.get('Peer') or {}).values():
            node_id = _stable_id(peer.get('ID'))
            reason = None
            if peer.get('OS') != 'iOS': reason = 'device_not_ios'
            elif type(peer.get('UserID')) is not int or peer.get('UserID') != user_id: reason = 'device_identity_changed'
            elif peer.get('Tags') or peer.get('AltSharerUserID'): reason = 'device_tagged_or_shared'
            elif peer.get('Expired', False) is not False or not _not_expired(peer.get('KeyExpiry')): reason = 'device_key_expired'
            elif type(peer.get('Online')) is not bool: _fail('tailscale_status_invalid')
            elif peer.get('Online') is not True: reason = 'device_offline'
            if reason:
                self.device_failures[node_id] = reason
                continue
            ips = tuple(tail_ip(ip) for ip in peer.get('TailscaleIPs', []))
            if ips:
                devices.append(Device(node_id, user_id, login,
                    str(peer.get('HostName', 'iPhone'))[:80], ips))
        # Cache only after the version AND complete live status were verified.
        # This is the CLI that serializes whois JSON, not just daemon metadata.
        self.verified_version = tuple(map(int, match.groups()))
        self.self_ips = tuple(tail_ip(ip) for ip in own.get('TailscaleIPs', []))
        return dns, devices

    def config(self):
        value = self.run('serve', 'status', '--json')
        if value is None:
            return {}
        if not isinstance(value, dict):
            _fail('serve_config_unknown')
        return value

    def whois(self, ip):
        return self.run('whois', '--json', tail_ip(ip))


def _empty_config(config):
    if not isinstance(config, dict):
        return False
    for key, value in config.items():
        if key == 'ETag':
            if value is not None and not isinstance(value, str): return False
        elif key not in {'TCP', 'Web', 'Foreground', 'AllowFunnel', 'Services'} or not (value is None or isinstance(value, dict) and not value):
            return False
    return True


def verify_owned_config(config, authority, target):
    """Require one exact foreground HTTPS proxy and no other routes/Funnel.

    Unknown nonempty config, background Serve and occupied ports are refused;
    never reset/overwrite an unrelated Serve/Funnel configuration.
    """
    if not isinstance(config, dict):
        return False
    outer = {k: v for k, v in config.items() if k != 'Foreground'}
    foreground = config.get('Foreground')
    if not _empty_config(outer) or not isinstance(foreground, dict) or len(foreground) != 1:
        return False
    inner = next(iter(foreground.values()))
    if not isinstance(inner, dict):
        return False
    remainder = {k: v for k, v in inner.items() if k not in ('TCP', 'Web')}
    return (_empty_config(remainder) and inner.get('TCP') == {str(HTTPS_PORT): {'HTTPS': True}} and
        inner.get('Web') == {authority: {'Handlers': {'/': {'Proxy': target}}}})


def config_failure(config, authority, target):
    """Classify only. This never normalizes away an unknown config field."""
    if not isinstance(config, dict):
        return 'serve_config_unknown'
    if _empty_config(config):
        return 'serve_config_missing'
    known = {'TCP', 'Web', 'Foreground', 'AllowFunnel', 'Services', 'ETag'}
    blocks = [config]
    foreground = config.get('Foreground')
    if foreground is not None:
        if not isinstance(foreground, dict):
            return 'serve_schema_unsupported'
        blocks.extend(foreground.values())
    for block in blocks:
        if not isinstance(block, dict) or set(block) - known:
            return 'serve_schema_unsupported'
        if any(value is not None and not isinstance(value, dict)
               for key, value in block.items() if key != 'ETag'):
            return 'serve_schema_unsupported'
    return None if verify_owned_config(config, authority, target) else 'serve_configuration_changed'


class TailnetGate:
    def __init__(self, cli, device):
        self.cli = cli
        self.device = device
        self.prefix = '/_goose_bridge_' + secrets.token_urlsafe(32)
        self.ready = False
        self.trace = RequestTrace()

    def verify(self, handler):
        # A candidate is not an authenticated device. Attribute only the
        # selected address through this run's private loopback proxy path.
        # Windows' self HTTPS probe has a different source and is not counted.
        values = handler.headers.get_all('X-Forwarded-For', [])
        candidate = (handler.client_address[0] == '127.0.0.1' and
                     handler.path.startswith(self.prefix + '/') and
                     len(values) == 1 and values[0] in self.device.ips)
        handler.trace_ticket = self.trace.candidate() if candidate else None
        try:
            result = self._verify_request(handler)
        except TailnetError as error:
            if error.code == 'cli_whois_timeout': code = 'phone_whois_timeout'
            elif error.code in ('cli_whois_json', 'cli_whois_encoding', 'identity_unavailable'): code = 'phone_whois_json_failed'
            elif error.code.startswith('cli_whois_'): code = 'phone_whois_cli_failed'
            elif error.code == 'device_denied': code = 'phone_identity_rejected'
            else: code = 'phone_proxy_headers_rejected'
            self.trace.record(handler.trace_ticket, code)
            raise
        self.trace.record(handler.trace_ticket, 'phone_identity_verified')
        return result

    def _verify_request(self, handler):
        def header(name):
            values = handler.headers.get_all(name, [])
            if len(values) != 1:
                _fail('proxy_header_denied')
            return values[0]
        if (not self.ready or handler.client_address[0] != '127.0.0.1' or
            not handler.path.startswith(self.prefix + '/') or
            header('X-Forwarded-Proto') != 'https' or
            header('X-Forwarded-Host') != handler.server.authority or
            handler.headers.get('Tailscale-Funnel-Request') is not None or
            handler.headers.get('Forwarded') is not None):
            _fail('transport_denied')
        ip = tail_ip(header('X-Forwarded-For'))  # Single, overwritten Serve value; no chains.
        if ip not in self.device.ips or header('Tailscale-User-Login') != self.device.login:
            _fail('device_denied')
        self.trace.record(handler.trace_ticket, 'phone_proxy_headers_ok')
        self.trace.record(handler.trace_ticket, 'phone_whois_started')
        binding = self.verify_identity(ip)
        handler.path = handler.path[len(self.prefix):]
        return binding

    def verify_identity(self, ip):
        """Revalidate the exact approved node; no proxy/header bypass.

        Tailscale 1.104.2's whois CLI encodes Node.KeyExpiry with omitzero.
        A missing field therefore represents disabled expiry ONLY for that
        verified CLI version. Explicit null is not the documented encoding.
        """
        ip = tail_ip(ip)
        if ip not in self.device.ips:
            _fail('device_denied')
        try:
            who = self.cli.whois(ip)
            node = who['Node']; user = who['UserProfile']
            addresses = [ipaddress.ip_network(a, strict=False) for a in node['Addresses']]
            if 'KeyExpiry' in node:
                expiry_valid = isinstance(node['KeyExpiry'], str) and _not_expired(node['KeyExpiry'])
            else:
                expiry_valid = getattr(self.cli, 'verified_version', None) == (1, 104, 2)
            if (node.get('StableID') != self.device.node_id or type(node.get('User')) is not int or
                node.get('User') != self.device.user_id or user.get('ID') != self.device.user_id or
                user.get('LoginName') != self.device.login or node.get('Tags') or
                node.get('MachineAuthorized') is not True or node.get('Expired', False) is not False or
                not expiry_valid or
                not any(ipaddress.ip_address(ip) in n and n.prefixlen == n.max_prefixlen for n in addresses)):
                _fail('device_denied')
        except TailnetError:
            raise
        except (KeyError, TypeError, AttributeError, ValueError):
            _fail('identity_unavailable')
        return self.device.node_id


class _WindowsJob:
    """Kill foreground Serve when this control process exits, even on a crash."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('ProcessTime', ctypes.c_int64), ('JobTime', ctypes.c_int64), ('Flags', w.DWORD),
                ('Min', ctypes.c_size_t), ('Max', ctypes.c_size_t), ('Processes', w.DWORD),
                ('Affinity', ctypes.c_size_t), ('Priority', w.DWORD), ('Scheduling', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('IO', IO), ('ProcessMemory', ctypes.c_size_t),
                ('JobMemory', ctypes.c_size_t), ('PeakProcess', ctypes.c_size_t), ('PeakJob', ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]; kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        limits = Extended(); limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, process._handle):
            if handle: kernel.CloseHandle(handle)
            _fail('serve_supervision_failed')
        self.kernel = kernel; self.handle = handle

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle); self.handle = None


class PhoneShare:
    def __init__(self, cli=None):
        self.cli = cli or Tailscale()
        self.server = None; self.thread = None; self.process = None; self.job = None
        self.monitor = None; self.cancel = threading.Event(); self.lock = threading.RLock()
        self.error = None; self.stop_confirmed = True
        self.cleanup_error = None

    def _record_failure(self, code):
        self.error = diagnostic_code(code)
        try:
            print(self.error, flush=True)
        except OSError:
            pass  # Diagnostics must never prevent revocation/cleanup.

    def _check_running(self, server, process, gate):
        """One fail-closed check; reason survives cleanup and UI rendering."""
        result = process.poll()
        if result is not None:
            _fail('serve_exited' if result == 0 else 'serve_failed')
        config = self.cli.config()
        reason = config_failure(config, server.authority, self.target)
        if reason:
            _fail(reason)
        if next(iter(config['Foreground'])) != self.serve_session:
            _fail('serve_session_changed')
        dns, devices = self.cli.inspect()
        if dns + ':' + str(HTTPS_PORT) != server.authority:
            _fail('tailnet_dns_changed')
        reason = device_check_failure(gate.device, devices, self.cli)
        if reason:
            _fail(reason)

    def start(self, p1_code, device, *, scenario='normal', snapshot_file=None, market_files=None):
        with self.lock:
            if self.server or self.process:
                _fail('already_running')
            self.error = None; self.cleanup_error = None; self.cancel = threading.Event()
            dns, devices = self.cli.inspect()
            reason = device_check_failure(device, devices, self.cli)
            if reason:
                _fail(reason)
            if not _empty_config(self.cli.config()):
                _fail('existing_serve_or_funnel')
            self.gate = TailnetGate(self.cli, device)
            if snapshot_file is None:
                if market_files is not None:
                    _fail('snapshot_required')
                self.server = create_server(p1_code, port=0, scenario=scenario)
            else:
                self.server = create_private_server(p1_code, snapshot_file, market_files,
                    gate=self.gate, authority=dns + ':' + str(HTTPS_PORT))
            self.server.transport = self.gate; self.server.secure_transport = True
            self.server.authority = dns + ':' + str(HTTPS_PORT)
            self.server.origin = 'https://' + self.server.authority
            self.server.cookie_name = '__Host-goose_v2_private' if self.server.real_data else '__Host-goose_v2_fixture'
            self.url = self.server.origin + '/readonly/'
            self.target = f'http://127.0.0.1:{self.server.server_port}' + self.gate.prefix
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
            try:
                # Foreground only: never --bg, funnel, reset, ACL mutation or login.
                self.process = subprocess.Popen(self.cli.command('serve', '--yes', '--https=' + str(HTTPS_PORT), self.target),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.job = _WindowsJob(self.process)
                self.stop_confirmed = False
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    result = self.process.poll()
                    if result is not None:
                        _fail('serve_exited' if result == 0 else 'serve_failed')
                    config = self.cli.config()
                    reason = config_failure(config, self.server.authority, self.target)
                    if reason and reason != 'serve_config_missing':
                        _fail(reason)
                    if verify_owned_config(config, self.server.authority, self.target):
                        self.serve_session = next(iter(config['Foreground']))
                        # Do not announce success before the same monitor checks
                        # have passed. Never enable requests while checks fail.
                        self._check_running(self.server, self.process, self.gate)
                        self.gate.ready = True
                        self.monitor = threading.Thread(target=self._watch,
                            args=(self.cancel, self.server, self.process, self.gate), daemon=True); self.monitor.start()
                        return self.url
                    self.cancel.wait(0.25)
                _fail('https_setup_required')
            except (OSError, ValueError, AttributeError, TypeError, KeyError) as error:
                code = error.code if isinstance(error, TailnetError) else 'https_start_failed'
                self._record_failure(code)
                self.stop()
                _fail(code)

    def _watch(self, cancel, server, process, gate):
        while not cancel.wait(2):
            try:
                self._check_running(server, process, gate)
            except (ValueError, OSError, AttributeError, TypeError, KeyError) as error:
                with self.lock:
                    if not cancel.is_set() and self.server is server:
                        self._record_failure(error.code if isinstance(error, TailnetError) else 'watch_internal_error')
                        self.stop()
                return

    def rotate(self):
        with self.lock:
            if not self.server or not self.gate.ready:
                _fail('not_running')
            return self.server.access.rotate()

    def check_connection(self):
        """Owner-only checks, while sharing. No selected files are reopened."""
        with self.lock:
            if not self.server or not self.gate.ready:
                return ('check_not_running',)
            gate = self.gate; server = self.server; cancel = self.cancel
            ticket = gate.trace.begin()
            authority = server.authority
            self_ips = getattr(self.cli, 'self_ips', ())
        codes = []
        try:
            gate.verify_identity(gate.device.ips[0])
            codes.append('check_whois_ok')
        except (ValueError, OSError) as error:
            codes.extend(('check_whois_failed', error.code if isinstance(error, TailnetError) else 'identity_unavailable'))
        if not cancel.is_set():
            codes.extend(check_local_https(authority, self_ips))
        # The browser can be refreshed during this window. Stops remain usable;
        # cancellation prevents observations/results from an old share leaking
        # into a newly started share.
        cancel.wait(10)
        if cancel.is_set():
            gate.trace.cancel()
            return ('check_cancelled',)
        codes.extend(gate.trace.finish(ticket))
        return tuple(dict.fromkeys(diagnostic_code(code) for code in codes))

    def stop(self):
        with self.lock:
            self.cancel.set()
            if self.server:
                self.gate.trace.cancel()
                self.gate.ready = False; self.server.access.close()
                self.server.shutdown(); self.server.server_close()
                if self.thread: self.thread.join(timeout=3)
                self.server = None; self.thread = None
            if self.process:
                try:
                    if self.process.poll() is None:
                        self.process.terminate()
                    self.process.wait(timeout=3)
                except (OSError, subprocess.SubprocessError):
                    self.process.kill(); self.process.wait(timeout=3)
                finally:
                    if self.job: self.job.close(); self.job = None
                    self.process = None
                try:
                    # No reset/off: never delete another application's routes.
                    self.stop_confirmed = _empty_config(self.cli.config())
                except (ValueError, OSError):
                    self.stop_confirmed = False
                if not self.stop_confirmed:
                    self.cleanup_error = 'stop_unconfirmed'
            return self.stop_confirmed
