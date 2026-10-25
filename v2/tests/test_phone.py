"""Fictional peers/CLI and original P1 fixtures only; no tailnet login or files.

These are contract, proxy-boundary and lifecycle tests, NOT proof of an actual
Tailscale tunnel or iPhone Safari acceptance.
"""
from copy import deepcopy
from email.message import Message
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import readonly_server as bridge
import tailscale_transport as phone

P1_CODE = os.environ.get('GOOSE_P1_CODE')
DEVICE = phone.Device('nFixturePhone', 101, 'fiction@example.invalid', '虚构iPhone', ('100.101.102.103', 'fd7a:115c:a1e0::103'))
AUTHORITY = 'fixture-pc.fixture-tail.ts.net:8443'


def identity():
    return {'Node': {'StableID': DEVICE.node_id, 'User': DEVICE.user_id,
        'Addresses': ['100.101.102.103/32', 'fd7a:115c:a1e0::103/128'], 'MachineAuthorized': True,
        'KeyExpiry': '2099-01-01T00:00:00Z'},
        'UserProfile': {'ID': DEVICE.user_id, 'LoginName': DEVICE.login}}


def status():
    return {'BackendState': 'Running', 'Self': {'ID': 'nFixturePC', 'Online': True,
        'DNSName': 'fixture-pc.fixture-tail.ts.net.', 'UserID': 101},
        'User': {'101': {'LoginName': DEVICE.login}},
        'Peer': {'nodekey:fixture': {'ID': DEVICE.node_id, 'Online': True, 'OS': 'iOS',
            'UserID': 101, 'HostName': DEVICE.name, 'TailscaleIPs': list(DEVICE.ips)}}}


def owned_config(target):
    return {'Foreground': {'fixture-session': {'TCP': {'8443': {'HTTPS': True}},
        'Web': {AUTHORITY: {'Handlers': {'/': {'Proxy': target}}}}}}}


class FakeCLI:
    def __init__(self): self.who = identity(); self.config_value = {}; self.commands = []
    def inspect(self): return AUTHORITY.removesuffix(':8443'), [DEVICE]
    def whois(self, ip): return deepcopy(self.who)
    def config(self): return deepcopy(self.config_value)
    def command(self, *args):
        self.commands.append(args)
        if args[0] == 'serve': self.config_value = owned_config(args[-1])
        return ['fixture-tailscale', *args]


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.cli = FakeCLI(); self.gate = phone.TailnetGate(self.cli, DEVICE); self.gate.ready = True
    def request(self, **changes):
        values = {'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': AUTHORITY,
            'X-Forwarded-For': DEVICE.ips[0], 'Tailscale-User-Login': DEVICE.login}
        values.update(changes)
        headers = Message()
        for k, v in values.items():
            if v is not None: headers[k] = v
        return SimpleNamespace(headers=headers, path=self.gate.prefix + '/api/v1/view',
            client_address=('127.0.0.1', 12345), server=SimpleNamespace(authority=AUTHORITY))
    def test_source_contract_allows_selected_ipv4_and_ipv6_only(self):
        for ip in DEVICE.ips:
            request = self.request(**{'X-Forwarded-For': ip})
            self.assertEqual(self.gate.verify(request), DEVICE.node_id)
            self.assertEqual(request.path, '/api/v1/view')
    def test_missing_spoofed_chained_or_plaintext_proxy_headers_fail_closed(self):
        for name, value in [('X-Forwarded-Proto', None), ('X-Forwarded-Proto', 'http'),
            ('X-Forwarded-Host', 'attacker.invalid'), ('X-Forwarded-For', '100.101.102.104'),
            ('X-Forwarded-For', '100.101.102.104, 100.101.102.103'), ('X-Forwarded-For', '127.0.0.1'),
            ('X-Forwarded-For', '192.168.1.2'), ('Tailscale-User-Login', None),
            ('Tailscale-User-Login', 'other@example.invalid'), ('Tailscale-Funnel-Request', '?1'),
            ('Forwarded', 'for=100.101.102.103')]:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.gate.verify(self.request(**{name: value}))
    def test_duplicate_header_backend_direct_path_and_nonloopback_are_denied(self):
        for change in ('duplicate', 'path', 'source', 'closed'):
            request = self.request()
            if change == 'duplicate': request.headers['X-Forwarded-For'] = DEVICE.ips[0]
            if change == 'path': request.path = '/api/v1/view'
            if change == 'source': request.client_address = ('100.101.102.103', 123)
            if change == 'closed': self.gate.ready = False
            with self.subTest(change=change), self.assertRaises(ValueError): self.gate.verify(request)
    def test_each_request_rechecks_node_user_key_expiry_and_authorization(self):
        cases = [('StableID', 'nOtherPhone'), ('User', 102), ('Tags', ['tag:server']),
            ('MachineAuthorized', False), ('MachineAuthorized', None), ('Expired', True),
            ('KeyExpiry', '2020-01-01T00:00:00Z'), ('KeyExpiry', 'unknown'), ('Addresses', ['100.101.102.104/32'])]
        for key, value in cases:
            self.cli.who = identity(); self.cli.who['Node'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.gate.verify(self.request())
        for key, value in [('ID', 102), ('LoginName', 'other@example.invalid')]:
            self.cli.who = identity(); self.cli.who['UserProfile'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.gate.verify(self.request())
    def test_whois_failure_is_not_authorized_and_does_not_leak_diagnostics(self):
        with patch.object(self.cli, 'whois', side_effect=phone.TailnetError('identity_unavailable')):
            with self.assertRaises(ValueError): self.gate.verify(self.request())
    def test_cookie_session_is_bound_to_stable_device_and_expires(self):
        now = [0]; access = bridge.Access({'fictional': True}, clock=lambda: now[0])
        token = access.pair(access.pair_code, DEVICE.node_id)
        self.assertTrue(access.authorized(token, DEVICE.node_id))
        self.assertFalse(access.authorized(token)); self.assertFalse(access.authorized(token, 'nOtherPhone'))
        now[0] = 900; self.assertFalse(access.authorized(token, DEVICE.node_id))
        access.close(); self.assertIsNone(access.view)
    def test_only_exact_foreground_https_config_is_accepted(self):
        target = 'http://127.0.0.1:12345/_fixture'
        good = owned_config(target)
        self.assertTrue(phone.verify_owned_config(good, AUTHORITY, target))
        bad = []
        for value in [None, {}, {'Foreground': {}}, {'TCP': {'8443': {'HTTPS': True}}}, {'TCP': False}, {'Web': []}]: bad.append(value)
        for key, value in [('AllowFunnel', {AUTHORITY: True}), ('Services', {'svc:unexpected': {}}),
            ('TCP', {'80': {'HTTP': True}}), ('unknown', True)]:
            c = deepcopy(good); c[key] = value; bad.append(c)
        c = deepcopy(good); c['Foreground']['fixture-session']['TCP']['8443'] = {'HTTP': True}; bad.append(c)
        c = deepcopy(good); c['Foreground']['fixture-session']['Web'][AUTHORITY]['Handlers']['/']['Proxy'] = 'http://attacker.invalid'; bad.append(c)
        c = deepcopy(good); c['Foreground']['fixture-session']['Web'][AUTHORITY]['Handlers']['/raw'] = {'Path': 'PRIVATE_CANARY'}; bad.append(c)
        c = deepcopy(good); c['Foreground']['another-session'] = {}; bad.append(c)
        for value in bad: self.assertFalse(phone.verify_owned_config(value, AUTHORITY, target))
    def test_cli_selection_requires_same_owner_online_ios_and_correct_domain(self):
        cli = object.__new__(phone.Tailscale)
        def inspect(s):
            with patch.object(cli, 'run', side_effect=['1.90.9\n', s]): return cli.inspect()
        self.assertEqual(inspect(status())[1], [DEVICE])
        for key, value in [('UserID', 102), ('OS', 'windows'), ('Online', False), ('Expired', True),
            ('Tags', ['tag:device']), ('AltSharerUserID', 102), ('KeyExpiry', '2000-01-01T00:00:00Z')]:
            s = status(); s['Peer']['nodekey:fixture'][key] = value
            self.assertEqual(inspect(s)[1], [])
        for name in ('attacker.invalid', 'pc.attacker.ts.net.evil', 'pc.ts.net', '127.0.0.1'):
            s = status(); s['Self']['DNSName'] = name
            with self.assertRaises(ValueError): inspect(s)
    def test_cli_does_not_echo_private_stderr_or_accept_old_version(self):
        cli = object.__new__(phone.Tailscale); cli.executable = 'fixture'
        with patch.object(phone.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout=b'PRIVATE_CANARY')):
            with self.assertRaises(ValueError) as error: cli.run('status', '--json')
            self.assertNotIn('PRIVATE_CANARY', str(error.exception))
        for version in ('1.52.0', '1.90.8', '1.92.0-unstable', 'unexpected'):
            with patch.object(cli, 'run', return_value=version), self.assertRaises(ValueError): cli.inspect()
    def test_malformed_cli_status_and_missing_node_expiry_are_not_accepted(self):
        cli = object.__new__(phone.Tailscale)
        for raw in [[], None, {'BackendState': 'Running', 'Self': []}, {'BackendState': 'Running', 'Self': {'Online': True, 'DNSName': None}}]:
            with patch.object(cli, 'run', side_effect=['1.90.9', raw]), self.assertRaises(ValueError): cli.inspect()
        del self.cli.who['Node']['KeyExpiry']
        with self.assertRaises(ValueError): self.gate.verify(self.request())
    @unittest.skipUnless(os.name == 'nt', 'Windows native owner panel')
    def test_windows_panel_builds_without_tailscale_or_personal_data(self):
        import tkinter as tk
        import phone_launcher
        with patch.object(tk.Tk, 'mainloop', lambda app: app.destroy()), patch.object(phone_launcher, 'Tailscale', side_effect=AssertionError('no automatic login')):
            self.assertEqual(phone_launcher.main(), 0)
    @unittest.skipUnless(os.name == 'nt', 'Windows crash cleanup job')
    def test_windows_job_closure_kills_owned_child(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            job = phone._WindowsJob(child); job.close()
            self.assertIsNotNone(child.wait(timeout=5))
        finally:
            if child.poll() is None: child.kill(); child.wait(timeout=5)


@unittest.skipUnless(P1_CODE, 'dedicated CI provides pinned P1 code')
class PhoneHttpTests(unittest.TestCase):
    def setUp(self):
        self.cli = FakeCLI(); self.server = bridge.create_server(P1_CODE, port=0)
        self.gate = phone.TailnetGate(self.cli, DEVICE); self.gate.ready = True
        self.server.transport = self.gate; self.server.secure_transport = True
        self.server.authority = AUTHORITY; self.server.origin = 'https://' + AUTHORITY
        self.server.cookie_name = '__Host-goose_v2_fixture'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)
    def request(self, path='/api/v1/view', method='GET', data=None, headers=None, prefix=True):
        # HTTP simulates only the trusted loopback hop after Serve TLS termination.
        h = {'Host': AUTHORITY, 'X-Forwarded-Host': AUTHORITY, 'X-Forwarded-Proto': 'https',
            'X-Forwarded-For': DEVICE.ips[0], 'Tailscale-User-Login': DEVICE.login}
        h.update(headers or {})
        if method == 'POST':
            h.setdefault('Content-Type', 'application/json'); h.setdefault('Origin', self.server.origin)
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.request(method, (self.gate.prefix if prefix else '') + path,
                json.dumps(data).encode() if data is not None else None, headers=h)
            response = conn.getresponse(); return response.status, dict(response.getheaders()), response.read()
        finally: conn.close()
    def login(self):
        code = self.server.access.pair_code
        status, headers, body = self.request('/session/pair', 'POST', {'code': code})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie']
        for flag in ('__Host-', 'Secure', 'HttpOnly', 'SameSite=Strict', 'Path=/'): self.assertIn(flag, cookie)
        self.assertNotIn(code, body.decode()); self.assertNotIn(self.server.access.session, body.decode())
        return {'Cookie': cookie.split(';')[0]}
    def test_https_cookie_fixture_view_no_demo_and_no_persistent_cache(self):
        auth = self.login(); status, headers, body = self.request(headers=auth)
        self.assertEqual(status, 200); self.assertTrue(json.loads(body)['test_data'])
        self.assertEqual(json.loads(body)['permissions'], {'valuation': False, 'profit_loss': False, 'advice': False})
        self.assertIn('no-store', headers['Cache-Control']); self.assertIn('Strict-Transport-Security', headers)
        status, _, body = self.request('/readonly/'); self.assertEqual(status, 200); self.assertNotIn(b'demo-data.js', body)
    def test_unknown_device_cannot_read_login_or_spend_pair_attempts(self):
        code = self.server.access.pair_code; auth = self.login()
        for path, method, data in [('/readonly/', 'GET', None), ('/api/v1/view', 'GET', None), ('/session/pair', 'POST', {'code': code})]:
            status, _, body = self.request(path, method, data, {**auth, 'X-Forwarded-For': '100.101.102.104'})
            self.assertEqual(status, 403); self.assertNotIn(b'accounts', body); self.assertNotIn(b'2500', body)
        self.assertEqual(self.server.access.failures, 0)
    def test_stolen_cookie_ip_reassignment_funnel_and_plaintext_hop_are_rejected(self):
        auth = self.login()
        for headers in [{'X-Forwarded-Proto': 'http'}, {'Tailscale-Funnel-Request': '?1'}, {'Origin': 'https://attacker.invalid'}]:
            self.assertEqual(self.request(headers={**auth, **headers})[0], 403)
        self.cli.who['Node']['StableID'] = 'nReassignedIP'
        self.assertEqual(self.request(headers=auth)[0], 403)
    def test_direct_backend_path_is_not_an_http_data_escape(self):
        auth = self.login()
        self.assertEqual(self.request(headers=auth, prefix=False)[0], 403)
        self.assertEqual(self.request(headers={**auth, 'Host': '127.0.0.1'})[0], 404)
    def test_revoke_expiry_close_and_late_response_cannot_reauthorize(self):
        auth = self.login(); self.server.access.rotate()
        self.assertEqual(self.request(headers=auth)[0], 401)
        auth = self.login(); self.server.access.expires = 0
        self.assertEqual(self.request(headers=auth)[0], 401)
        self.gate.ready = False; self.server.access.close()
        self.assertEqual(self.request(headers=auth)[0], 403); self.assertIsNone(self.server.access.view)


@unittest.skipUnless(P1_CODE, 'dedicated CI provides pinned P1 code')
class LifecycleTests(unittest.TestCase):
    def test_foreground_serve_start_stop_closes_backend_and_preserves_external_config(self):
        cli = FakeCLI(); share = phone.PhoneShare(cli)
        process = SimpleNamespace(poll=lambda: None, terminate=lambda: setattr(cli, 'config_value', {}), wait=lambda **kw: 0)
        with patch.object(phone.subprocess, 'Popen', return_value=process), patch.object(phone, '_WindowsJob'):
            try:
                url = share.start(P1_CODE, DEVICE)
                self.assertEqual(url, 'https://' + AUTHORITY + '/readonly/')
                old = share.server.access; token = old.pair(old.pair_code, DEVICE.node_id)
                self.assertTrue(share.gate.ready); self.assertTrue(old.authorized(token, DEVICE.node_id))
                self.assertNotIn('--bg', cli.commands[0]); self.assertEqual(cli.commands[0][0], 'serve')
                self.assertEqual(share.server.server_address[0], '127.0.0.1')
                self.assertTrue(share.stop()); self.assertIsNone(old.view); self.assertFalse(old.authorized(token, DEVICE.node_id))
                self.assertFalse(share.gate.ready)
            finally: share.stop()
    def test_existing_serve_funnel_or_wrong_device_refuses_before_loading_p1(self):
        for config, selected in [({'AllowFunnel': {AUTHORITY: True}}, DEVICE), ({'TCP': {'443': {'HTTPS': True}}}, DEVICE), ({}, phone.Device('nOther', 101, DEVICE.login, '其他', DEVICE.ips))]:
            cli = FakeCLI(); cli.config_value = config; share = phone.PhoneShare(cli)
            with patch.object(phone, 'create_server', side_effect=AssertionError('must not load P1')), self.assertRaises(ValueError):
                share.start(P1_CODE, selected)
            self.assertEqual(cli.config_value, config); self.assertEqual(cli.commands, [])
    def test_start_failure_cleans_fixture_listener_and_never_enables_real_flag(self):
        cli = FakeCLI(); share = phone.PhoneShare(cli)
        with patch.object(phone.subprocess, 'Popen', side_effect=OSError('PRIVATE_CANARY')):
            with self.assertRaises(ValueError) as error: share.start(P1_CODE, DEVICE)
            self.assertNotIn('PRIVATE_CANARY', str(error.exception)); self.assertIsNone(share.server)
        with patch.object(bridge, 'REAL_DATA_ENABLED', True), self.assertRaises(ValueError): share.start(P1_CODE, DEVICE)
    def test_monitor_detects_proxy_reconfiguration_and_revokes_session(self):
        cli = FakeCLI(); share = phone.PhoneShare(cli)
        process = SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=lambda **kw: 0)
        with patch.object(phone.subprocess, 'Popen', return_value=process), patch.object(phone, '_WindowsJob'):
            try:
                share.start(P1_CODE, DEVICE); old = share.server.access
                cli.config_value = {'AllowFunnel': {AUTHORITY: True}}
                deadline = time.monotonic() + 5
                while share.server is not None and time.monotonic() < deadline: time.sleep(.05)
                self.assertIsNone(share.server); self.assertIsNone(old.view)
                self.assertFalse(share.stop_confirmed); self.assertTrue(share.error)
                self.assertEqual(cli.config_value, {'AllowFunnel': {AUTHORITY: True}})
                self.assertTrue(all(command[0] == 'serve' for command in cli.commands))
            finally: share.stop()


if __name__ == '__main__': unittest.main()
