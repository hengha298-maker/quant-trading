"""Windows owner-panel connection check controls; no P1 files or network."""
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
import phone_launcher as launcher
from tailscale_transport import diagnostic_code
from test_phone import AUTHORITY, FakeCLI


@unittest.skipUnless(os.name == 'nt', 'Windows native connection diagnostic controls')
class ConnectionControlTests(unittest.TestCase):
    def _callTestMethod(self, method):
        # Quantitative/optional native libraries imported earlier by pytest can
        # affect Tcl initialization in the shared process. Each case still runs
        # its full native GUI scenario, in a fresh Python rather than skipping.
        flag = 'GOOSE_CONNECTION_GUI_CHILD'
        case = self._testMethodName
        if os.environ.get(flag) == case:
            return super()._callTestMethod(method)
        environment = os.environ.copy(); environment[flag] = case
        try:
            result = subprocess.run(
                [sys.executable, '-X', 'utf8', '-m', 'unittest',
                 f'test_connection_controls.ConnectionControlTests.{case}', '-v'],
                cwd=Path(__file__).resolve().parent, env=environment,
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=40)
        except subprocess.TimeoutExpired:
            self.fail('Native connection-control GUI child exceeded 40 seconds')
        details = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, 'Native GUI child failed:\n' + details)
        self.assertIn('Ran 1 test', result.stderr, 'Native GUI case was not executed:\n' + details)
        self.assertNotIn('skipped=', result.stderr, 'Native GUI case must execute, not skip:\n' + details)

    def run_panel(self, scenario, *, results=None):
        import tkinter as tk
        from tkinter import ttk
        instances = []; starts = []
        result_codes = results or ('check_whois_ok', 'check_local_https_ok', 'phone_page_sent')
        class Share:
            def __init__(self):
                self.error = None; self.server = None; self.stop_confirmed = True
                self.entered = threading.Event(); self.release = threading.Event()
                self.completed = threading.Event(); self.stopped = threading.Event()
                self.worker_id = None
                instances.append(self)
            def start(self, folder, device, **kwargs):
                starts.append((folder, device, kwargs))
                self.server = SimpleNamespace(access=SimpleNamespace(pair_code='01234567'))
                self.gate = SimpleNamespace(ready=True)
                return 'https://' + AUTHORITY + '/readonly/'
            def check_connection(self):
                self.worker_id = threading.get_ident(); self.entered.set()
                try:
                    if not self.release.wait(8):
                        return ('check_cancelled',)
                    return result_codes
                finally:
                    self.completed.set()
            def stop(self):
                self.server = None; self.stopped.set()
                return True

        class Panel:
            def __init__(self, app): self.app = app
            def widgets(self, parent=None):
                for child in (parent or self.app).winfo_children():
                    yield child; yield from self.widgets(child)
            def button(self, title):
                return next(w for w in self.widgets() if isinstance(w, ttk.Button) and w.cget('text') == title)
            def wait(self, predicate):
                deadline = time.monotonic() + 5
                while not predicate() and time.monotonic() < deadline:
                    self.app.update(); time.sleep(.01)
                self.app.update()
                if not predicate(): raise AssertionError('native control operation did not complete')
            def settle(self):
                deadline = time.monotonic() + .4
                while time.monotonic() < deadline:
                    self.app.update(); time.sleep(.01)
            def status(self):
                widget = next(w for w in self.widgets() if isinstance(w, ttk.Label) and str(w.cget('wraplength')) == '670')
                return str(self.app.getvar(str(widget.cget('textvariable'))))
            def url(self):
                return next(w for w in self.widgets() if isinstance(w, ttk.Entry) and str(w.cget('width')) == '85').get()
            def select_and_start(self):
                self.button('1. 选择P1源码').invoke()
                self.button('2. 选择持仓快照').invoke()
                self.button('3. 选择行情（可多选）').invoke()
                self.button('刷新设备').invoke()
                picker = next(w for w in self.widgets() if isinstance(w, ttk.Combobox) and str(w.cget('width')) == '80')
                self.wait(lambda: bool(picker.cget('values'))); picker.current(0)
                self.start(1)
            def start(self, count):
                self.button('启动分享').invoke()
                self.wait(lambda: len(starts) == count and self.url().startswith('https://') and str(self.button('启动分享').cget('state')) == 'normal')
            def check(self):
                self.button('检查手机连接（同时刷新Safari）').invoke()
                self.wait(lambda: instances[-1].entered.is_set())
                return instances[-1]
            def stop(self):
                owner = instances[-1]
                self.button('停止分享').invoke()
                self.wait(lambda: owner.stopped.is_set() and self.url() == '已停止' and str(self.button('启动分享').cget('state')) == 'normal')

        def loop(app):
            panel = Panel(app)
            try: scenario(panel, instances, starts)
            finally:
                for owner in instances: owner.release.set()
                for owner in instances:
                    if owner.entered.is_set(): owner.completed.wait(1)
                app.destroy()

        with patch.object(tk.Tk, 'mainloop', loop), patch.object(launcher, 'Tailscale', FakeCLI), patch.object(launcher, 'PhoneShare', Share), \
             patch('tkinter.filedialog.askdirectory', return_value='PRIVATE_SOURCE_CANARY') as source, \
             patch('tkinter.filedialog.askopenfilename', return_value='PRIVATE_SNAPSHOT_CANARY') as snapshot, \
             patch('tkinter.filedialog.askopenfilenames', return_value=('PRIVATE_MARKET_A', 'PRIVATE_MARKET_B')) as markets, \
             patch('tkinter.messagebox.askyesno', return_value=True):
            self.assertEqual(launcher.main(real=True), 0)
        source.assert_called_once(); snapshot.assert_called_once(); markets.assert_called_once()

    def test_connection_check_runs_in_background_and_stop_keeps_file_selections(self):
        def scenario(panel, instances, starts):
            panel.select_and_start()
            owner = panel.check()
            self.assertNotEqual(owner.worker_id, threading.get_ident())
            self.assertFalse(owner.completed.is_set())
            self.assertEqual(str(panel.button('停止分享').cget('state')), 'normal')
            panel.stop()
            self.assertIsNone(owner.server)
            self.assertFalse(owner.completed.is_set())
            owner.release.set(); panel.wait(owner.completed.is_set); panel.settle()
            panel.start(2)
            self.assertEqual(starts[0], starts[1])
        self.run_panel(scenario)

    def test_completed_diagnostic_copies_only_multiline_fixed_codes(self):
        secret = 'PRIVATE_COOKIE_URL_CANARY https://private.invalid/path?code=01234567'
        results = ('check_whois_ok', 'check_local_https_ok', 'phone_candidate_arrived',
                   'phone_identity_verified', 'phone_page_sent', secret)
        def scenario(panel, instances, starts):
            panel.select_and_start(); owner = panel.check()
            owner.release.set()
            panel.wait(lambda: owner.completed.is_set() and '完整固定码可复制' in panel.status())
            panel.button('复制诊断码（仅固定代码）').invoke()
            copied = panel.app.clipboard_get()
            self.assertEqual(copied, '\n'.join((launcher.BUILD_CODE, *(diagnostic_code(code) for code in results))))
            self.assertGreater(len(copied.splitlines()), 1)
            for private in ('PRIVATE', AUTHORITY, '01234567', 'Cookie', 'https://'):
                self.assertNotIn(private, copied)
            self.assertEqual(len(starts), 1); self.assertIsNotNone(owner.server)
        self.run_panel(scenario, results=results)

    def test_late_old_diagnostic_cannot_overwrite_restarted_share_or_clipboard(self):
        def scenario(panel, instances, starts):
            panel.select_and_start(); previous = panel.check()
            panel.stop(); panel.start(2)
            current = instances[-1]
            self.assertIsNot(current, previous)
            expected_status = panel.status(); expected_url = panel.url()
            self.assertIn('真实持仓只读分享已启动', expected_status)
            panel.app.clipboard_clear(); panel.app.clipboard_append('UNCHANGED_TEST_SENTINEL')
            previous.release.set(); panel.wait(previous.completed.is_set); panel.settle()
            self.assertEqual(panel.status(), expected_status)
            self.assertEqual(panel.url(), expected_url)
            self.assertEqual(panel.app.clipboard_get(), 'UNCHANGED_TEST_SENTINEL')
            panel.button('复制诊断码（仅固定代码）').invoke()
            self.assertEqual(panel.app.clipboard_get(), launcher.BUILD_CODE + '\ndiag_no_result')
            self.assertIsNotNone(current.server); self.assertEqual(starts[0], starts[1])
        self.run_panel(scenario, results=('phone_identity_rejected',))


if __name__ == '__main__': unittest.main()
