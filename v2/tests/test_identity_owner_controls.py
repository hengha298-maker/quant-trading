"""Native owner preflight reporting, with fictional selections and no phone.

Each GUI case runs in its own Python process so optional native libraries
imported earlier by full-project pytest cannot influence Tcl initialization.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phone_launcher as launcher
from tailscale_transport import TailnetError
from identity_diagnostics import WHOIS_CODES
from test_phone import FakeCLI


@unittest.skipUnless(os.name == 'nt', 'Windows native identity owner controls')
class IdentityOwnerControlsTests(unittest.TestCase):
    def _callTestMethod(self, method):
        flag = 'GOOSE_IDENTITY_OWNER_GUI_CHILD'
        case = self._testMethodName
        if os.environ.get(flag) == case:
            return super()._callTestMethod(method)
        environment = os.environ.copy(); environment[flag] = case
        try:
            result = subprocess.run(
                [sys.executable, '-X', 'utf8', '-m', 'unittest',
                 f'test_identity_owner_controls.IdentityOwnerControlsTests.{case}', '-v'],
                cwd=Path(__file__).resolve().parent, env=environment,
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=40)
        except subprocess.TimeoutExpired:
            self.fail('Native identity-owner GUI child exceeded 40 seconds')
        details = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, 'Native GUI child failed:\n' + details)
        self.assertIn('Ran 1 test', result.stderr, 'Native GUI case did not execute:\n' + details)
        self.assertNotIn('skipped=', result.stderr, 'Native GUI case must execute:\n' + details)

    def test_failed_start_copies_all_identity_conditions_without_phone_or_reselection(self):
        import tkinter as tk
        from tkinter import ttk
        failures = ('whois_stable_id_mismatch', 'whois_machine_authorized_missing',
                    'whois_address_mismatch')
        self.assertTrue(set(failures) <= WHOIS_CODES)
        starts = []; instances = []

        class Share:
            def __init__(self):
                self.identity_report = failures
                self.server = None; self.error = None; self.stop_confirmed = True
                instances.append(self)
            def start(self, folder, device, **kwargs):
                starts.append((folder, device, kwargs))
                # Match real PhoneShare._record_failure. The recurring pump
                # must not overwrite the complete report with only this code.
                self.error = self.identity_report[0]
                raise TailnetError(self.identity_report[0])
            def stop(self):
                self.server = None
                return True

        def loop(app):
            def widgets(parent):
                for child in parent.winfo_children():
                    yield child; yield from widgets(child)
            def button(title):
                return next(w for w in widgets(app) if isinstance(w, ttk.Button) and w.cget('text') == title)
            def url():
                return next(w for w in widgets(app) if isinstance(w, ttk.Entry) and str(w.cget('width')) == '85').get()
            def wait(predicate):
                deadline = time.monotonic() + 5
                while not predicate() and time.monotonic() < deadline:
                    app.update(); time.sleep(.01)
                app.update()
                self.assertTrue(predicate(), 'native owner-preflight operation did not complete')
            def start_and_copy(count):
                button('启动分享').invoke()
                wait(lambda: len(starts) == count and url() == '已停止' and str(button('启动分享').cget('state')) == 'normal')
                button('复制诊断码（仅固定代码）').invoke()
                copied = app.clipboard_get()
                self.assertEqual(copied, '\n'.join(failures))
                for secret in ('PRIVATE_', 'https://', 'Cookie', '01234567'):
                    self.assertNotIn(secret, copied)
                self.assertIsNone(instances[-1].server)

            try:
                button('1. 选择P1源码').invoke()
                button('2. 选择持仓快照').invoke()
                button('3. 选择行情（可多选）').invoke()
                button('刷新设备').invoke()
                picker = next(w for w in widgets(app) if isinstance(w, ttk.Combobox) and str(w.cget('width')) == '80')
                wait(lambda: bool(picker.cget('values'))); picker.current(0)
                start_and_copy(1)
                # A second click retries the same explicit files, with no phone
                # refresh, diagnostic-window race, or additional file picker.
                start_and_copy(2)
                self.assertEqual(starts[0], starts[1])
            finally:
                app.destroy()

        with patch.object(tk.Tk, 'mainloop', loop), patch.object(launcher, 'Tailscale', FakeCLI), \
             patch.object(launcher, 'PhoneShare', Share), \
             patch('tkinter.filedialog.askdirectory', return_value='PRIVATE_SOURCE_CANARY') as source, \
             patch('tkinter.filedialog.askopenfilename', return_value='PRIVATE_SNAPSHOT_CANARY') as snapshot, \
             patch('tkinter.filedialog.askopenfilenames', return_value=('PRIVATE_MARKET_A', 'PRIVATE_MARKET_B')) as markets, \
             patch('tkinter.messagebox.askyesno', return_value=True):
            self.assertEqual(launcher.main(real=True), 0)
        source.assert_called_once(); snapshot.assert_called_once(); markets.assert_called_once()


if __name__ == '__main__':
    unittest.main()
