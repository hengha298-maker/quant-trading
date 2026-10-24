"""Explicit temporary JSON files only. Never access the owner's P1 data."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from p1_dependency import load_p1
from readonly_private import build_private_view
from readonly_fixture import fictional_inputs
import readonly_server as bridge
import tailscale_transport as phone
from test_phone import AUTHORITY, DEVICE, FakeCLI
import test_phone as phone_tests

P1_CODE = os.environ.get('GOOSE_P1_CODE')


class PrivateFiles:
    def setup_files(self):
        self.temp = tempfile.TemporaryDirectory(prefix='goose-selected-json-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / '.local-only').touch()
        (self.base / 'snapshots').mkdir(); (self.base / 'market').mkdir()
        self.p1 = load_p1(P1_CODE)
        data, markets = fictional_inputs('normal', datetime.now(timezone.utc))
        data['accounts'][0]['account'] = 'PRIVATE_INTERNAL_CANARY'
        data['accounts'][0]['positions'][0].update(quantity=337, available=119, broker_cost='23.456')
        data['accounts'][0]['cash'] = '0'
        data['accounts'][1]['positions'][0]['quantity'] = 7333
        markets[0]['rows'][0].update(open='22.718', high='22.718', low='22.718', close='22.718')
        markets[0]['url'] = 'https://example.invalid/PRIVATE_URL_CANARY'
        self.snapshot = self.base / 'snapshots' / 'chosen.json'
        self.markets = [self.base / 'market' / f'chosen-{i}.json' for i in range(2)]
        self.snapshot.write_text(json.dumps(data), encoding='utf-8')
        for f, document in zip(self.markets, markets):
            f.write_text(json.dumps(document), encoding='utf-8')
        # Invalid unrelated JSON must never be opened or used as fallback.
        (self.base / 'market' / 'unselected.json').write_text('PRIVATE_UNSELECTED_CANARY', encoding='utf-8')


@unittest.skipUnless(P1_CODE, 'dedicated CI requires pinned original P1 code')
class PrivateProviderTests(PrivateFiles, unittest.TestCase):
    def setUp(self): self.setup_files()
    def view(self, markets=None):
        return build_private_view(self.p1, self.snapshot, self.markets if markets is None else markets)
    def test_reads_only_selected_original_p1_files_and_preserves_unknowns_and_source(self):
        before = {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in self.base.rglob('*') if f.is_file()}
        with patch.object(self.p1.storage, 'read_private', wraps=self.p1.storage.read_private) as read, \
             patch.object(self.p1.model, 'build_view_model', wraps=self.p1.model.build_view_model) as model, \
             patch.object(self.p1.usage, 'market_usage', wraps=self.p1.usage.market_usage) as usage:
            view = self.view()
        # Windows Temp may use RUNNER~1; P1 intentionally canonicalizes it.
        self.assertEqual([c.args[0] for c in read.call_args_list],
                         [f.resolve(strict=True) for f in [self.snapshot, *self.markets]])
        self.assertFalse(model.call_args.kwargs['demo']); self.assertEqual(usage.call_count, 2)
        self.assertFalse(view['test_data']); self.assertTrue(view['bundle_id'].startswith('private-'))
        a, b = view['accounts']
        self.assertEqual(a['positions'][0], dict(symbol='600900.SH', kind='stock', quantity=337, available=119, broker_cost='23.456'))
        self.assertEqual(a['cash'], '0'); self.assertIsNone(b['cash']); self.assertIsNone(b['positions'][0]['broker_cost'])
        self.assertEqual(b['positions'][0]['quantity'], 7333); self.assertEqual(view['known_cash'], '0')
        self.assertEqual(view['quotes'][0]['close'], '22.718')
        self.assertEqual(view['quotes'][1]['state'], 'observation'); self.assertIsNone(view['quotes'][1]['close_unit'])
        self.assertFalse(view['quotes'][1]['historical_cost_reference'])
        self.assertEqual(view['permissions'], dict(valuation=False, profit_loss=False, advice=False)); self.assertIsNone(view['total_assets'])
        for secret in ('PRIVATE_', str(self.base), 'response_sha256', 'provenance', 'volume', 'raw_rows'):
            self.assertNotIn(secret, json.dumps(view))
        self.assertEqual(before, {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in self.base.rglob('*') if f.is_file()})
    def test_missing_market_never_reads_any_old_or_demo_prices(self):
        view = self.view([])
        self.assertTrue(all(q['close'] is None for q in view['quotes']))
        self.assertTrue(all(q['state'] == 'missing' for q in view['quotes']))
    def test_malformed_duplicate_nan_future_and_invalid_positions_are_rejected_without_leaks(self):
        original = self.snapshot.read_text(encoding='utf-8')
        for bad in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}', '{', '[]'):
            self.snapshot.write_text(bad, encoding='utf-8')
            with self.assertRaises(ValueError) as error: self.view()
            self.assertNotIn(str(self.base), str(error.exception))
        for mutate in (lambda d: d.update(as_of='2999-01-01'),
                       lambda d: d['accounts'][0]['positions'][0].update(available=338),
                       lambda d: d['accounts'][1].update(coverage='complete')):
            data = json.loads(original); mutate(data)
            self.snapshot.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError): self.view()
    def test_invalid_ohlc_and_duplicate_selected_files_do_not_fall_back(self):
        with self.assertRaises(ValueError): self.view([self.markets[0], self.markets[0]])
        data = json.loads(self.markets[0].read_text(encoding='utf-8')); data['rows'][0]['low'] = '999'
        self.markets[0].write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError): self.view()
    def test_ambiguous_raw_daily_batches_are_not_auto_selected(self):
        other = self.base / 'market' / 'other.json'; other.write_bytes(self.markets[1].read_bytes())
        q = next(q for q in self.view([self.markets[1], other])['quotes'] if q['symbol'] == '515450.SH')
        self.assertEqual(q['state'], 'ambiguous'); self.assertIsNone(q['close'])
    def test_minute_input_cannot_replace_daily(self):
        data = json.loads(self.markets[0].read_text(encoding='utf-8'))
        data['interval'] = 'minute'
        data['rows'][0]['time'] += ' 09:00'
        data['observed_start'] = data['observed_end'] = data['rows'][0]['time']
        self.markets[0].write_text(json.dumps(data), encoding='utf-8')
        self.assertIsNone(self.view([self.markets[0]])['quotes'][0]['close'])
    def test_missing_marker_cross_root_cloud_git_and_unc_are_rejected(self):
        marker = self.base / '.local-only'; marker.unlink()
        with self.assertRaises(ValueError): self.view()
        marker.touch()
        with tempfile.TemporaryDirectory() as other:
            f = Path(other) / 'foreign.json'; f.write_bytes(self.markets[0].read_bytes())
            with self.assertRaises(ValueError): self.view([f])
        for folder in ('OneDrive', 'repo'):
            base = self.base / folder; (base / 'snapshots').mkdir(parents=True); (base / '.local-only').touch()
            if folder == 'repo':
                (base / '.git').mkdir(); (base / '.git' / 'HEAD').write_text('ref: refs/heads/fake')
            f = base / 'snapshots' / 'a.json'; f.write_bytes(self.snapshot.read_bytes())
            with self.assertRaises(ValueError): build_private_view(self.p1, f, [])
        with self.assertRaises(ValueError): build_private_view(self.p1, r'\\server\share\secret.json', [])
    def test_oversized_input_rejected_before_p1_read_and_no_raw_error_output(self):
        with self.snapshot.open('wb') as f: f.truncate(32_000_001)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), \
             patch.object(self.p1.storage, 'read_private', side_effect=AssertionError('must not parse')), self.assertRaises(ValueError):
            self.view()
        self.assertEqual(output.getvalue(), '')
    def test_real_path_never_imports_fixture_or_demo_modules(self):
        script = """import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))
from readonly_server import create_private_server
from tailscale_transport import TailnetGate
from test_phone import FakeCLI, DEVICE, AUTHORITY
# test_phone imports the fixture harness but never its data module.
assert 'readonly_fixture' not in sys.modules
s=create_private_server(sys.argv[1],sys.argv[2],[],gate=TailnetGate(FakeCLI(),DEVICE),authority=AUTHORITY)
try:
 assert s.access.view['test_data'] is False
 assert 'readonly_fixture' not in sys.modules
finally: s.server_close()
"""
        result = subprocess.run([sys.executable, '-c', script, P1_CODE, str(self.snapshot)],
                                capture_output=True, cwd=Path(__file__).parent)
        self.assertEqual(result.returncode, 0, 'private path must not import fictional inputs')
    @unittest.skipUnless(os.name == 'nt', 'Windows native private owner panel')
    def test_windows_real_panel_builds_with_required_buttons_without_automatic_read(self):
        import tkinter as tk
        from tkinter import ttk
        import phone_launcher
        def inspect(app):
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child; yield from descendants(child)
            titles = {w.cget('text') for w in descendants(app) if isinstance(w, ttk.Button)}
            self.assertTrue({'启动分享', '撤销授权', '停止分享', '2. 选择持仓快照'} <= titles)
            app.destroy()
        with patch.object(tk.Tk, 'mainloop', inspect), patch.object(phone_launcher, 'PhoneShare', side_effect=AssertionError('no automatic read')):
            self.assertEqual(phone_launcher.main(real=True), 0)


@unittest.skipUnless(P1_CODE, 'dedicated CI requires pinned original P1 code')
class PrivateHttpTests(PrivateFiles, phone_tests.PhoneHttpTests):
    # Inherit ALL device/auth/expiry/Funnel/direct-backend tests for real file mode.
    def setUp(self):
        self.setup_files(); self.cli = FakeCLI()
        self.gate = phone.TailnetGate(self.cli, DEVICE)
        self.server = bridge.create_private_server(P1_CODE, self.snapshot, self.markets, gate=self.gate, authority=AUTHORITY)
        self.gate.ready = True
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
    def test_https_cookie_fixture_view_no_demo_and_no_persistent_cache(self):
        auth = self.login(); status, headers, body = self.request(headers=auth)
        self.assertEqual(status, 200); self.assertFalse(json.loads(body)['test_data'])
        self.assertEqual(headers['X-Goose-Test-Only'], 'false'); self.assertIn('no-store', headers['Cache-Control'])
        status, _, html = self.request('/readonly/')
        self.assertEqual(status, 200); self.assertIn(b'data-private="true"', html); self.assertNotIn(b'demo-data.js', html)
        self.snapshot.write_text('changed after explicit publication', encoding='utf-8')
        self.assertEqual(self.request(headers=auth)[2], body)  # Immutable memory batch; no hidden reread.
    def test_real_server_loses_transport_fails_closed_even_with_valid_cookie(self):
        auth = self.login()
        for attr, value in [('secure_transport', False), ('transport', None)]:
            original = getattr(self.server, attr); setattr(self.server, attr, value)
            self.assertEqual(self.request(headers=auth)[0], 403)
            setattr(self.server, attr, original)
    def test_private_constructor_refuses_missing_gate_before_reading(self):
        with patch.object(bridge, 'load_p1', side_effect=AssertionError('no reads')), self.assertRaises(ValueError):
            bridge.create_private_server(P1_CODE, self.snapshot, [], gate=None, authority=AUTHORITY)
    def test_real_start_never_uses_fixture_provider_and_stop_erases_batch(self):
        cli = FakeCLI(); share = phone.PhoneShare(cli)
        from types import SimpleNamespace
        process = SimpleNamespace(poll=lambda: None, terminate=lambda: setattr(cli, 'config_value', {}), wait=lambda **kw: 0)
        with patch.object(phone, 'create_server', side_effect=AssertionError('no fixture fallback')), \
             patch.object(phone.subprocess, 'Popen', return_value=process), patch.object(phone, '_WindowsJob'):
            try:
                share.start(P1_CODE, DEVICE, snapshot_file=self.snapshot, market_files=self.markets)
                access = share.server.access; self.assertFalse(access.view['test_data'])
                self.assertTrue(share.stop()); self.assertIsNone(access.view); self.assertIsNone(access.session)
            finally: share.stop()


if __name__ == '__main__': unittest.main()
