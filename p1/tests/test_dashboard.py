"""Fictional UI, unknown-value and local file-picker safety regressions."""
import copy
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import report, report_demo, report_model, report_wizard, storage
NOW = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)


class Structure(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.advanced = False
        self.pre_outside = 0
        self.scripts = 0
        self.open_advanced = False
        self.external = []
        self.details_depth = 0
        self.advanced_depth = None
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'details':
            self.details_depth += 1
        if tag == 'details' and attrs.get('id') == 'advanced':
            self.advanced = True
            self.advanced_depth = self.details_depth
            self.open_advanced = 'open' in attrs
        if tag == 'pre' and not self.advanced:
            self.pre_outside += 1
        if tag in ('script', 'iframe', 'object', 'form'):
            self.scripts += 1
        if 'src' in attrs or ('href' in attrs and not attrs['href'].startswith('#')):
            self.external.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'details':
            if self.details_depth == self.advanced_depth:
                self.advanced = False
                self.advanced_depth = None
            self.details_depth -= 1


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.markets = report_demo.fixtures()
    def model(self, markets=None):
        return report_model.build_view_model(self.snapshot, self.markets if markets is None else markets, generated_at=NOW)

    def test_unknown_and_real_zero_are_distinct_without_total_assets(self):
        model = self.model()
        self.assertEqual(model['recorded_cash'], dict(amount='12980.50', known_accounts=1, unknown_accounts=1))
        for key in ('market_value', 'profit_loss', 'advice'):
            self.assertIsNone(model['holdings'][1][key])
        self.assertIsNone(model['total_assets']['amount'])
        for a in self.snapshot['accounts']: a['cash'] = '0'
        self.assertEqual(self.model()['recorded_cash']['amount'], '0')
        for a in self.snapshot['accounts']: a['cash'] = None
        self.assertIsNone(self.model()['recorded_cash']['amount'])

    def test_view_model_does_not_mutate_or_alias_inputs(self):
        original = copy.deepcopy((self.snapshot, self.markets))
        model = self.model()
        model['accounts'][0]['cash'] = '99'
        model['holdings'][0]['positions'][0]['quantity'] = 99
        model['audit'][1]['provenance']['usage'] = 'changed'
        self.assertEqual((self.snapshot, self.markets), original)
        self.assertEqual(model['view_version'], 1)
        self.assertIsNone(model['snapshot_at'])
        self.assertEqual(model['extensions'], dict(asset_distribution=None, history=None, dividends=None))
        self.assertNotIn('snapshot_path', model)

    def test_sina_observation_cannot_price_cost_or_valuation(self):
        model = self.model()
        self.assertEqual(model['holdings'][0]['quote']['state'], 'reference')
        self.assertEqual(model['holdings'][1]['quote']['state'], 'observation')
        self.assertFalse(model['holdings'][1]['quote']['historical_cost_reference'])
        with patch.object(report, 'scenario', side_effect=AssertionError('Sina cannot price any scenario')):
            html = report.render(self.snapshot, [self.markets[1]])
        self.assertIn('源站原始收盘数值 · 单位未核验', html)
        self.assertIn('未计算市值与盈亏', html)

    def test_three_timestamps_are_separate_and_old_data_stays_old(self):
        model = self.model()
        self.assertEqual(model['snapshot_as_of'], '2026-10-20')
        self.assertEqual(model['holdings'][0]['quote']['latest_date'], '2026-10-20')
        self.assertEqual(model['holdings'][0]['quote']['fetched_at'], '2026-10-20T09:05:00+00:00')
        self.assertEqual(model['holdings'][0]['quote']['lag_days'], 3)
        self.assertEqual(model['generated_at'], NOW.isoformat())

    def test_qualified_primary_etf_displays_reference_not_sina_restriction(self):
        primary = copy.deepcopy(self.markets[0])
        primary['symbol'] = '515450.SH'
        model = self.model([primary])
        self.assertEqual(model['holdings'][1]['quote']['state'], 'reference')
        html = report.render(self.snapshot, [primary])
        self.assertIn('515450 · 历史收盘参考', html)
        self.assertNotIn('源站原始收盘数值 · 单位未核验', html)

    def test_mixed_primary_and_sina_batch_ambiguity_cannot_price_hidden_scenarios(self):
        primary = copy.deepcopy(self.markets[0])
        primary['symbol'] = '515450.SH'
        with patch.object(report, 'scenario', side_effect=AssertionError('ambiguous batch cannot price')):
            html = report.render(self.snapshot, [primary, self.markets[1]])
        self.assertIn('行情批次待确认', html)

    def test_missing_failed_minute_and_ambiguous_batches_do_not_choose_prices(self):
        for markets, state in [([], 'missing'), ([self.markets[1], copy.deepcopy(self.markets[1])], 'ambiguous')]:
            q = self.model(markets)['holdings'][1]['quote']
            self.assertEqual(q['state'], state)
            self.assertIsNone(q['close'])
        failed = dict(schema_version=1, symbol='515450.SH', interval='daily', source='无可用来源', adjustment='不复权', fetched_at='2026-10-20T09:05:00+00:00', requested_start='2026-10-19', requested_end='2026-10-20', status='unavailable', rows=[], error_type='AllSourcesUnavailable')
        self.assertEqual(self.model([failed])['holdings'][1]['quote']['state'], 'unavailable')
        self.assertIn('本次采集未取得通过校验的日线', report.render(self.snapshot, [failed]))
        minute = copy.deepcopy(self.markets[0])
        minute.update(interval='minute', observed_start='2026-10-20 14:50', observed_end='2026-10-20 14:50')
        minute['rows'][0]['time'] = '2026-10-20 14:50'
        self.assertEqual(self.model([minute])['holdings'][0]['quote']['state'], 'missing')

    def test_unrecorded_focus_is_unknown_and_other_symbols_are_kept(self):
        p = self.snapshot['accounts'][0]['positions'][0]
        p['symbol'] = '000001.SZ'
        self.snapshot['accounts'][0]['positions'] = [p]
        self.snapshot['accounts'][1]['positions'] = []
        model = self.model([])
        self.assertEqual([c['symbol'] for c in model['holdings']], ['600900.SH', '515450.SH', '000001.SZ'])
        self.assertIsNone(model['holdings'][0]['quantity'])
        self.assertEqual(model['holdings'][2]['quantity'], 400)
        self.assertIn('未录入该标的持仓，不代表实际未持有', report.render(self.snapshot, []))

    def test_technical_blocks_are_closed_advanced_only_without_external_resources(self):
        html = report.render(self.snapshot, self.markets)
        structure = Structure(html)
        self.assertEqual(structure.pre_outside, 0)
        self.assertFalse(structure.open_advanced)
        self.assertEqual(structure.scripts, 0)
        self.assertEqual(structure.external, [])
        self.assertIn("default-src 'none'", html)
        self.assertIn('JSON 来源审计', html)
        missing = report.render(self.snapshot, [])
        self.assertIn('尚未附加行情文件，暂无审计数据', missing)
        self.assertNotIn('<pre>[]</pre>', missing)

    def test_untrusted_labels_remain_text_not_executable_html(self):
        self.snapshot['accounts'][0]['broker'] = '<img src=x onerror=alert(1)>'
        self.markets[0]['source'] = '<script>alert(2)</script>'
        html = report.render(self.snapshot, self.markets)
        self.assertIn('&lt;img', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('<script', html)

    def test_demo_label_is_explicit_and_never_fills_missing_real_data(self):
        html = report.render(self.snapshot, self.markets, demo=True)
        self.assertIn('虚构演示 · 非真实账户', html)
        self.assertIn('以下审计字段全部虚构', html)
        for a in self.snapshot['accounts']: a['positions'] = []
        html = report.render(self.snapshot, [])
        self.assertNotIn('虚构演示 · 非真实账户', html)
        self.assertNotIn('28.36', html)
        self.assertNotIn('1.333', html)

    def test_wizard_preserves_input_hashes_and_rejects_outside_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = storage.initialize(Path(temp) / '本地演示', True)
            snap = storage.save(base, 'snapshots', self.snapshot)
            quotes = storage.save(base, 'market', self.markets[1])
            before = {p: sha256(p.read_bytes()).hexdigest() for p in (snap, quotes)}
            first = report_wizard.create_report(snap, [quotes])
            second = report_wizard.create_report(snap, [])
            self.assertNotEqual(first, second)
            for p, digest in before.items(): self.assertEqual(sha256(p.read_bytes()).hexdigest(), digest)
            outside = Path(temp) / 'outside.json'
            outside.write_text(json.dumps(self.markets[0]), encoding='utf-8')
            with self.assertRaises(ValueError): report_wizard.create_report(snap, [outside])
            with self.assertRaises(ValueError): report_wizard.create_report(quotes, [])
            self.assertEqual(len(list((base / 'reports').glob('*.html'))), 2)

    def test_actual_demo_cli_without_git_or_utf8_console(self):
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, PATH='', PYTHONIOENCODING='cp1252', TMPDIR=temp, TEMP=temp, TMP=temp)
            result = subprocess.run([sys.executable, '-m', 'goose_research.report_demo', '--no-open'], cwd=str(Path(report.__file__).resolve().parents[1]), env=env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            dest = Path(result.stdout.decode('utf-8').splitlines()[-1])
            self.assertIn(Path(temp).resolve(), dest.resolve().parents)
            self.assertIn('虚构演示 · 非真实账户', dest.read_text(encoding='utf-8'))
            self.assertEqual(len(list(Path(temp).rglob('*.json'))), 0)

    @unittest.skipUnless(os.name == 'nt', 'native Windows Tk window smoke test')
    def test_native_windows_window_builds_without_reading_files_or_browser(self):
        import tkinter as tk
        with patch.object(tk.Tk, 'mainloop', lambda app: app.destroy()), patch.object(report_wizard, 'generate', side_effect=AssertionError('no selection')), patch.object(report_wizard.webbrowser, 'open', side_effect=AssertionError('no report')):
            self.assertEqual(report_wizard.main(), 0)


if __name__ == '__main__': unittest.main()
