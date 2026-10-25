"""1.104.2 status contracts and deterministic fail-closed supervision.

All peers, process results, URLs, paths and asset inputs are fictional.
"""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tailscale_transport as phone
import phone_launcher as launcher
from test_phone import AUTHORITY, DEVICE, FakeCLI, owned_config, status

P1_CODE = os.environ.get('GOOSE_P1_CODE')


class Status104Tests(unittest.TestCase):
    def inspect(self, document, version='1.104.2'):
        cli = object.__new__(phone.Tailscale)
        with patch.object(cli, 'run', side_effect=[version, document]):
            dns, devices = cli.inspect()
        return cli, dns, devices

    def test_1042_display_name_and_ip_order_are_not_device_identity(self):
        document = status(); peer = document['Peer']['nodekey:fixture']
        peer['HostName'] = 'localhost'; peer['TailscaleIPs'].reverse()
        cli, dns, devices = self.inspect(document)
        self.assertEqual(dns + ':8443', AUTHORITY)
        self.assertIsNone(phone.device_check_failure(DEVICE, devices, cli))
        self.assertIsNone(phone.device_check_failure(DEVICE, [replace(DEVICE, name='renamed')]))
        for version in ('1.90.9', '1.104.2'):
            self.assertTrue(self.inspect(status(), version)[2])

    def test_full_identity_and_address_set_remain_strict(self):
        for changed in (replace(DEVICE, node_id='nOther'), replace(DEVICE, user_id=202),
                        replace(DEVICE, login='other@example.invalid'), replace(DEVICE, ips=DEVICE.ips[:1]),
                        replace(DEVICE, ips=(*DEVICE.ips, '100.101.102.104'))):
            self.assertIsNotNone(phone.device_check_failure(DEVICE, [changed]))

    def test_peer_offline_identity_expiry_and_device_type_have_distinct_codes(self):
        for key, value, code in [('Online', False, 'device_offline'), ('OS', 'windows', 'device_not_ios'),
                                ('UserID', 202, 'device_identity_changed'), ('Expired', True, 'device_key_expired'),
                                ('KeyExpiry', '2000-01-01T00:00:00Z', 'device_key_expired'),
                                ('Tags', ['tag:test'], 'device_tagged_or_shared'),
                                ('AltSharerUserID', 202, 'device_tagged_or_shared')]:
            document = status(); document['Peer']['nodekey:fixture'][key] = value
            cli, _, devices = self.inspect(document)
            self.assertEqual(phone.device_check_failure(DEVICE, devices, cli), code)

    def test_self_offline_expiry_dns_and_schema_are_distinct(self):
        for field, value, code in [('Online', False, 'tailscale_self_offline'),
                                  ('Expired', True, 'tailscale_self_key_expired'),
                                  ('DNSName', 'invalid.example', 'magicdns_required'),
                                  ('Online', 'true', 'tailscale_status_invalid')]:
            document = status(); document['Self'][field] = value
            with self.assertRaises(phone.TailnetError) as error: self.inspect(document)
            self.assertEqual(error.exception.code, code)
        document = status(); document['Self']['DNSName'] = document['Self']['DNSName'].upper()
        self.assertEqual(self.inspect(document)[1], AUTHORITY.removesuffix(':8443'))
        document = status(); document['Peer']['nodekey:fixture']['Online'] = 'true'
        with self.assertRaises(phone.TailnetError) as error: self.inspect(document)
        self.assertEqual(error.exception.code, 'tailscale_status_invalid')

    def test_serve_1042_foreground_json_is_checked_without_human_status(self):
        target = 'http://127.0.0.1:12345/_fictional'
        cli = object.__new__(phone.Tailscale)
        for value in (owned_config(target), {**owned_config(target), 'Services': None}):
            with patch.object(cli, 'run', return_value=value) as run:
                self.assertTrue(phone.verify_owned_config(cli.config(), AUTHORITY, target))
            run.assert_called_once_with('serve', 'status', '--json')
        self.assertEqual(phone.config_failure({}, AUTHORITY, target), 'serve_config_missing')
        self.assertEqual(phone.config_failure([], AUTHORITY, target), 'serve_config_unknown')
        changed = owned_config(target); changed['FutureFormat'] = True
        self.assertEqual(phone.config_failure(changed, AUTHORITY, target), 'serve_schema_unsupported')
        changed = owned_config(target); changed['AllowFunnel'] = {AUTHORITY: True}
        self.assertEqual(phone.config_failure(changed, AUTHORITY, target), 'serve_configuration_changed')

    def test_cli_failure_codes_are_fixed_and_do_not_collapse_timeout_exit_json(self):
        cli = object.__new__(phone.Tailscale); cli.executable = 'PRIVATE_PATH_CANARY'
        for operation, args in [('status', ('status', '--json')), ('config', ('serve', 'status', '--json')),
                                ('version', ('version',)), ('whois', ('whois', '--json', DEVICE.ips[0]))]:
            cases = [(subprocess.TimeoutExpired('PRIVATE_CANARY', 6, output=b'PRIVATE_CANARY'), None, 'timeout'),
                     (OSError('PRIVATE_CANARY'), None, 'launch'),
                     (None, SimpleNamespace(returncode=1, stdout=b'PRIVATE_CANARY'), 'exit'),
                     (None, SimpleNamespace(returncode=0, stdout=b'PRIVATE_CANARY'), 'json'),
                     (None, SimpleNamespace(returncode=0, stdout=b'\xff'), 'encoding')]
            for failure, result, reason in cases:
                with patch.object(phone.subprocess, 'run', side_effect=failure, return_value=result), self.assertRaises(phone.TailnetError) as error:
                    cli.run(*args)
                self.assertEqual(str(error.exception), f'cli_{operation}_{reason}')
                self.assertNotIn('PRIVATE', str(error.exception))
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}'):
            with patch.object(phone.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=raw)), self.assertRaises(phone.TailnetError) as error:
                cli.run('status', '--json')
            self.assertEqual(error.exception.code, 'cli_status_json')

    def test_untrusted_exception_text_cannot_become_console_or_clipboard_diagnostic(self):
        secret = 'PRIVATE_CANARY https://fixture.invalid/path?code=12345678'
        share = phone.PhoneShare(FakeCLI()); output = io.StringIO()
        with redirect_stdout(output): share._record_failure(secret)
        self.assertEqual(output.getvalue(), 'transport_internal_error\n')
        self.assertEqual(str(phone.TailnetError(secret)), 'transport_internal_error')
        self.assertNotIn('PRIVATE', launcher.error_message(secret))

    def test_refresh_retains_only_the_same_authorized_identity(self):
        self.assertEqual(launcher.refreshed_choice(DEVICE, {'new display': replace(DEVICE, name='localhost', ips=tuple(reversed(DEVICE.ips)))}), 'new display')
        self.assertEqual(launcher.refreshed_choice(DEVICE, {'same display': replace(DEVICE, node_id='nOther')}), '')

    @unittest.skipUnless(os.name == 'nt', 'Windows native retry/clipboard')
    def test_windows_retry_keeps_explicit_file_choices_and_copies_only_error_code(self):
        import tkinter as tk
        from tkinter import ttk
        starts = []
        class Share:
            error = None; server = None; stop_confirmed = True
            def start(self, folder, device, **kw):
                starts.append((folder, device, kw))
                if len(starts) == 1: raise phone.TailnetError('device_offline')
                self.server = SimpleNamespace(access=SimpleNamespace(pair_code='01234567'))
                self.gate = SimpleNamespace(ready=True)
                return 'https://' + AUTHORITY + '/readonly/'
            def stop(self): self.server = None; return True
        def loop(app):
            def widgets(parent):
                for child in parent.winfo_children():
                    yield child; yield from widgets(child)
            def button(title): return next(w for w in widgets(app) if isinstance(w, ttk.Button) and w.cget('text') == title)
            def wait_for(predicate):
                deadline = time.monotonic() + 5
                while not predicate() and time.monotonic() < deadline:
                    app.update(); time.sleep(.01)
                self.assertTrue(predicate())
            button('1. 选择P1源码').invoke(); button('2. 选择持仓快照').invoke(); button('3. 选择行情（可多选）').invoke()
            button('刷新设备').invoke()
            picker = next(w for w in widgets(app) if isinstance(w, ttk.Combobox) and w.cget('width') == 80)
            wait_for(lambda: len(picker.cget('values')) > 0); picker.current(0)
            button('启动分享').invoke(); wait_for(lambda: len(starts) == 1 and str(button('启动分享').cget('state')) == 'normal')
            button('复制诊断码（仅固定代码）').invoke(); self.assertEqual(app.clipboard_get(), launcher.BUILD_CODE + '\ndevice_offline')
            button('启动分享').invoke(); wait_for(lambda: len(starts) == 2 and str(button('启动分享').cget('state')) == 'normal')
            self.assertEqual(starts[0], starts[1]); app.destroy()
        with patch.object(tk.Tk, 'mainloop', loop), patch.object(launcher, 'Tailscale', FakeCLI), patch.object(launcher, 'PhoneShare', Share), \
             patch('tkinter.filedialog.askdirectory', return_value='FICTIONAL_CODE') as code, \
             patch('tkinter.filedialog.askopenfilename', return_value='FICTIONAL_SNAPSHOT') as snap, \
             patch('tkinter.filedialog.askopenfilenames', return_value=('FICTIONAL_MARKET_A', 'FICTIONAL_MARKET_B')) as markets, \
             patch('tkinter.messagebox.askyesno', return_value=True):
            self.assertEqual(launcher.main(real=True), 0)
        code.assert_called_once(); snap.assert_called_once(); markets.assert_called_once()


@unittest.skipUnless(P1_CODE, 'dedicated CI provides pinned P1 sources')
class MonitorLifecycleTests(unittest.TestCase):
    def start_share(self):
        cli = FakeCLI(); share = phone.PhoneShare(cli)
        process = SimpleNamespace(result=None)
        process.poll = lambda: process.result
        process.terminate = lambda: setattr(cli, 'config_value', {})
        process.wait = lambda **kw: 0
        with patch.object(phone.subprocess, 'Popen', return_value=process), patch.object(phone, '_WindowsJob'), patch.object(phone.PhoneShare, '_watch'):
            share.start(P1_CODE, DEVICE)
        self.addCleanup(share.stop)
        return share, cli, process

    def test_metadata_only_changes_survive_actual_monitor_and_request_identity_stays_bound(self):
        share, cli, process = self.start_share()
        cli.inspect = lambda: (AUTHORITY.removesuffix(':8443'), [replace(DEVICE, name='localhost', ips=tuple(reversed(DEVICE.ips)))])
        for _ in range(3): share._check_running(share.server, process, share.gate)
        self.assertTrue(share.gate.ready); self.assertIsNone(share.error)

    def test_each_monitor_branch_revokes_clears_and_preserves_exact_sanitized_reason(self):
        cases = ['serve_exited', 'serve_failed', 'serve_config_missing', 'serve_configuration_changed',
                 'serve_schema_unsupported', 'serve_session_changed', 'tailnet_dns_changed', 'device_unavailable',
                 'device_identity_changed', 'device_addresses_changed', 'cli_config_timeout',
                 'cli_status_json', 'tailscale_self_offline', 'device_offline', 'watch_internal_error']
        class Tick:
            calls = 0
            def wait(self, _seconds):
                self.calls += 1
                return self.calls > 1
            def is_set(self): return False
        for expected in cases:
            with self.subTest(expected=expected):
                share, cli, process = self.start_share(); access = share.server.access
                token = access.pair(access.pair_code, DEVICE.node_id)
                if expected == 'serve_exited': process.result = 0
                elif expected == 'serve_failed': process.result = 1
                elif expected == 'serve_config_missing': cli.config_value = {}
                elif expected == 'serve_configuration_changed': cli.config_value['AllowFunnel'] = {AUTHORITY: True}
                elif expected == 'serve_schema_unsupported': cli.config_value['FutureFormat'] = True
                elif expected == 'serve_session_changed': cli.config_value['Foreground'] = {'new-session': next(iter(cli.config_value['Foreground'].values()))}
                elif expected == 'tailnet_dns_changed': cli.inspect = lambda: ('changed.fixture-tail.ts.net', [DEVICE])
                elif expected == 'device_unavailable': cli.inspect = lambda: (AUTHORITY.removesuffix(':8443'), [])
                elif expected == 'device_identity_changed': cli.inspect = lambda: (AUTHORITY.removesuffix(':8443'), [replace(DEVICE, user_id=202)])
                elif expected == 'device_addresses_changed': cli.inspect = lambda: (AUTHORITY.removesuffix(':8443'), [replace(DEVICE, ips=DEVICE.ips[:1])])
                elif expected == 'device_offline':
                    cli.device_failures = {DEVICE.node_id: 'device_offline'}
                    cli.inspect = lambda: (AUTHORITY.removesuffix(':8443'), [])
                else:
                    failure = OSError('PRIVATE_EXCEPTION_CANARY') if expected == 'watch_internal_error' else phone.TailnetError(expected)
                    if expected == 'cli_config_timeout': cli.config = lambda failure=failure: (_ for _ in ()).throw(failure)
                    else: cli.inspect = lambda failure=failure: (_ for _ in ()).throw(failure)
                output = io.StringIO()
                with redirect_stdout(output): share._watch(Tick(), share.server, process, share.gate)
                self.assertEqual(share.error, expected); self.assertEqual(output.getvalue(), expected + '\n')
                self.assertIsNone(share.server); self.assertFalse(share.gate.ready)
                self.assertIsNone(access.view); self.assertFalse(access.authorized(token, DEVICE.node_id))
                self.assertNotIn('PRIVATE', output.getvalue())
                self.assertNotIn(AUTHORITY, output.getvalue())

    def test_start_does_not_announce_success_if_first_full_check_fails(self):
        cli = FakeCLI(); share = phone.PhoneShare(cli)
        process = SimpleNamespace(poll=lambda: None, terminate=lambda: setattr(cli, 'config_value', {}), wait=lambda **kw: 0)
        with patch.object(phone.subprocess, 'Popen', return_value=process), patch.object(phone, '_WindowsJob'), \
             patch.object(share, '_check_running', side_effect=phone.TailnetError('device_offline')), redirect_stdout(io.StringIO()), \
             self.assertRaises(phone.TailnetError) as error:
            share.start(P1_CODE, DEVICE)
        self.assertEqual(error.exception.code, 'device_offline'); self.assertIsNone(share.server)


if __name__ == '__main__': unittest.main()
