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

HTTPS_PORT = 8443


class TailnetError(ValueError):
    """Only fixed diagnostic codes; never echo CLI output, paths or identities."""


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
        try:
            result = subprocess.run(self.command(*args), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode or len(result.stdout) > 2_000_000:
                _fail('tailscale_command_failed')
            text = result.stdout.decode('utf-8')
            return json.loads(text) if json_result else text
        except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
            _fail('tailscale_unavailable')

    def inspect(self):
        try:
            return self._inspect()
        except (KeyError, IndexError, TypeError, AttributeError):
            _fail('tailscale_status_invalid')

    def _inspect(self):
        version = self.run('version', json_result=False).splitlines()[0]
        match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version.strip())
        if not match or tuple(map(int, match.groups())) < (1, 90, 9):
            _fail('tailscale_version_unsupported')
        status = self.run('status', '--json')
        if not isinstance(status, dict) or status.get('BackendState') != 'Running':
            _fail('tailscale_not_connected')
        own = status.get('Self', {})
        if not isinstance(own, dict) or own.get('Online') is not True or own.get('Expired', False) is not False or not _not_expired(own.get('KeyExpiry')):
            _fail('tailscale_not_connected')
        dns = own.get('DNSName', '').removesuffix('.')
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ts\.net', dns):
            _fail('magicdns_required')
        user_id = own.get('UserID')
        users = status.get('User') or {}
        login = users.get(str(user_id), {}).get('LoginName')
        if type(user_id) is not int or user_id <= 0 or not isinstance(login, str) or not login.isascii() or not login or len(login) > 254:
            _fail('personal_identity_required')
        devices = []
        for peer in (status.get('Peer') or {}).values():
            if (peer.get('OS') != 'iOS' or peer.get('UserID') != user_id or peer.get('Online') is not True or
                peer.get('Expired', False) is not False or not _not_expired(peer.get('KeyExpiry')) or
                peer.get('Tags') or peer.get('AltSharerUserID')):
                continue
            ips = tuple(tail_ip(ip) for ip in peer.get('TailscaleIPs', []))
            if ips:
                devices.append(Device(_stable_id(peer.get('ID')), user_id, login,
                    str(peer.get('HostName', 'iPhone'))[:80], ips))
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


class TailnetGate:
    def __init__(self, cli, device):
        self.cli = cli
        self.device = device
        self.prefix = '/_goose_bridge_' + secrets.token_urlsafe(32)
        self.ready = False

    def verify(self, handler):
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
        try:
            who = self.cli.whois(ip)
            node = who['Node']; user = who['UserProfile']
            addresses = [ipaddress.ip_network(a, strict=False) for a in node['Addresses']]
            if (node.get('StableID') != self.device.node_id or type(node.get('User')) is not int or
                node.get('User') != self.device.user_id or user.get('ID') != self.device.user_id or
                user.get('LoginName') != self.device.login or node.get('Tags') or
                node.get('MachineAuthorized') is not True or node.get('Expired', False) is not False or
                not isinstance(node.get('KeyExpiry'), str) or not _not_expired(node['KeyExpiry']) or
                not any(ipaddress.ip_address(ip) in n and n.prefixlen == n.max_prefixlen for n in addresses)):
                _fail('device_denied')
        except (KeyError, TypeError, AttributeError):
            _fail('identity_unavailable')
        handler.path = handler.path[len(self.prefix):]
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

    def start(self, p1_code, device, *, scenario='normal', snapshot_file=None, market_files=None):
        with self.lock:
            if self.server or self.process:
                _fail('already_running')
            self.error = None; self.cancel = threading.Event()
            dns, devices = self.cli.inspect()
            if device not in devices:
                _fail('device_selection_expired')
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
                    if self.process.poll() is not None:
                        _fail('serve_start_failed')
                    if verify_owned_config(self.cli.config(), self.server.authority, self.target):
                        self.gate.ready = True
                        self.monitor = threading.Thread(target=self._watch,
                            args=(self.cancel, self.server, self.process, self.gate), daemon=True); self.monitor.start()
                        return self.url
                    self.cancel.wait(0.25)
                _fail('https_setup_required')
            except (OSError, ValueError):
                self.stop()
                _fail('https_start_failed')

    def _watch(self, cancel, server, process, gate):
        while not cancel.wait(2):
            try:
                if process.poll() is not None:
                    _fail('serve_exited')
                if not verify_owned_config(self.cli.config(), server.authority, self.target):
                    _fail('serve_configuration_changed')
                dns, devices = self.cli.inspect()
                if dns + ':' + str(HTTPS_PORT) != server.authority or gate.device not in devices:
                    _fail('device_or_tailnet_changed')
            except (ValueError, OSError, AttributeError):
                with self.lock:
                    if not cancel.is_set() and self.server is server:
                        self.error = '连接或设备状态改变，已撤销会话并停止分享。'
                        self.stop()
                return

    def rotate(self):
        with self.lock:
            if not self.server or not self.gate.ready:
                _fail('not_running')
            return self.server.access.rotate()

    def stop(self):
        with self.lock:
            self.cancel.set()
            if self.server:
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
            return self.stop_confirmed
