import csv
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import io
import os
import subprocess
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research.storage import initialize, local_path, save
from goose_research.holdings import FIELDS, parse, import_csv
from goose_research.fees import scenario
from goose_research.market import fetch, three_years_ago
from goose_research.report import render, generate
from goose_research.__main__ import main


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = initialize(Path(self.tmp.name) / 'private', True)
        self.csv = self.base / 'input.csv'
        self.row = ['FAKE', '华宝虚构', '2026-01-05', 'partial', '', '600900.SH', 'stock', '200', '100', '']
        self.write([self.row])

    def write(self, rows):
        with self.csv.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(FIELDS)
            w.writerows(rows)

    def test_unknown_cost_partial_and_immutable_history(self):
        one = import_csv(self.csv, self.base)
        original = one.read_bytes()
        self.assertNotEqual(one, import_csv(self.csv, self.base))
        self.assertEqual(one.read_bytes(), original)
        doc = json.loads(original)
        self.assertIsNone(doc['accounts'][0]['positions'][0]['broker_cost'])
        self.assertFalse(doc['transaction_history_complete'])

    def test_invalid_import_no_snapshot(self):
        for index, value in [(7, 'NaN'), (7, '-1'), (7, '1.5'), (8, '300'), (9, 'Infinity'), (5, '600900'), (3, 'all'), (2, '2099-01-01'), (0, '真实账号!')]:
            with self.subTest(index=index, value=value):
                row = self.row.copy()
                row[index] = value
                self.write([row])
                with self.assertRaises(ValueError):
                    import_csv(self.csv, self.base)
                self.assertFalse((self.base / 'snapshots').exists())

    def test_duplicate_and_conflicting_metadata(self):
        self.write([self.row, self.row])
        with self.assertRaises(ValueError):
            parse(self.csv)
        other = self.row.copy()
        other[5], other[4] = '515450.SH', '10000'
        self.write([self.row, other])
        with self.assertRaises(ValueError):
            parse(self.csv)

    def test_multiple_complete_accounts_cash_only(self):
        row = self.row.copy()
        row[3:5] = ['complete', '10000']
        self.write([row, ['FAKE2', '银河虚构', '2026-01-05', 'complete', '20000', '', '', '', '', '']])
        self.assertEqual(len(parse(self.csv)['accounts']), 2)
        row[4] = ''
        self.write([row])
        with self.assertRaises(ValueError):
            parse(self.csv)

    def test_private_paths(self):
        for p in (Path(__file__).resolve().parents[1] / 'private', Path(self.tmp.name) / 'OneDrive' / 'x'):
            with self.assertRaises(ValueError):
                local_path(p)
        with self.assertRaises(ValueError):
            initialize(Path(self.tmp.name) / 'other')
        with patch.dict('os.environ', {'OneDrive': self.tmp.name}):
            with self.assertRaises(ValueError):
                local_path(self.base)

    def test_symlink(self):
        link = self.base / 'link'
        try:
            link.symlink_to(Path(__file__).resolve().parents[1], target_is_directory=True)
        except OSError:
            self.skipTest('OS denies symlinks')
        with self.assertRaises(ValueError):
            local_path(link / 'private')

    def test_fees_and_both_directions(self):
        for kind, profile, expected in [('stock', 'huabao_example', '3.12'), ('etf', 'yinhe_etf_example', '2.40')]:
            result = scenario(kind, 10, 100, 100, '10000', 'buy_then_sell', profile)
            self.assertEqual(Decimal(result['round_trip_cost']), Decimal(expected))
        for direction in ('buy_then_sell', 'sell_then_buy'):
            for available, cash in [(0, '10000'), (100, None), (100, '0')]:
                self.assertEqual(scenario('stock', 10, 100, available, cash, direction, 'huabao_example')['status'], 'unavailable')
        with self.assertRaises(ValueError):
            scenario('stock', 10, 99, 100, 10000, 'buy_then_sell', 'huabao_example')

    def feed(self, lines):
        return lambda *a, **k: io.BytesIO(json.dumps({'data': {'code': '600900', 'market': 1, 'klines': lines}}).encode())

    def test_market_validation(self):
        line = '2026-01-05,10,11,12,9,100'
        now = datetime(2026, 1, 5, 17, tzinfo=timezone(timedelta(hours=8)))
        result = fetch('600900.SH', 'daily', self.feed([line]), now=now)
        self.assertEqual((result['status'], result['count']), ('observed', 1))
        for rows in ([], [line, line], ['2026-01-05,10,11,8,9,100'], ['2026-01-05,NaN,11,12,9,100'], ['2099-01-01,10,11,12,9,100']):
            self.assertEqual(fetch('600900.SH', 'daily', self.feed(rows), now=now)['status'], 'unavailable')
        self.assertEqual(three_years_ago(date(2024, 2, 29)), date(2021, 2, 28))

    def test_minutes_and_failure(self):
        self.assertEqual(fetch('600900.SH', 'minute', self.feed(['2026-01-05 09:31,10,11,12,9,100']))['count'], 1)
        def fail(*a, **kw):
            raise OSError('sensitive remote body')
        result = fetch('600900.SH', 'daily', fail)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('sensitive', json.dumps(result))

    def test_report_escape_offline_and_missing_data(self):
        doc = parse(self.csv)
        doc['accounts'][0]['broker'] = '<script>alert(1)</script>'
        html = render(doc, [])
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        for text in ('不是整个真实账户', '无完整流水', '不可用', '费用假设', '行情事实', 'default-src'):
            self.assertIn(text, html)
        snap = save(self.base, 'snapshots', doc)
        report = generate(self.base, snap, [])
        self.assertTrue(report.exists())
        self.assertNotEqual(report, generate(self.base, snap, []))

    def test_negative_broker_cost_and_success_report(self):
        row = self.row.copy()
        row[9], row[4] = '-2.5', '10000'
        self.write([row])
        doc = parse(self.csv)
        self.assertEqual(doc['accounts'][0]['positions'][0]['broker_cost'], '-2.5')
        m = fetch('600900.SH', 'daily', self.feed(['2026-01-05,10,11,12,9,100']),
                  now=datetime(2026, 1, 5, 17, tzinfo=timezone(timedelta(hours=8))))
        html = render(doc, [m])
        self.assertIn('先买后卖', html)
        self.assertIn('先卖后买', html)
        self.assertIn('往返费用', html)
        with self.assertRaises(ValueError):
            render(doc, [m, m])

    def test_guard_rejects_private_artifacts(self):
        from check_private import check
        # A unit test of Git mode must supply its own metadata marker; the
        # user's source may be a ZIP with no .git or Git executable at all.
        source = Path(self.tmp.name) / 'fictional-source'
        (source / '.git').mkdir(parents=True)
        with patch('check_private.REPO', source):
            with patch('check_private.subprocess.check_output', return_value=b'notes.csv\0'):
                self.assertEqual(check(), 1)
            with patch('check_private.subprocess.check_output', return_value=b'config.example.json\0p1/goose_research/market.py\0'):
                self.assertEqual(check(True), 0)

    def test_non_utf8_redirected_console(self):
        env = dict(os.environ, PYTHONIOENCODING='cp1252')
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'check_private.py'), '--all'], env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('检查通过', result.stdout.decode('utf-8'))
        result = subprocess.run([sys.executable, '-m', 'goose_research', '--data-dir', str(self.base), 'template'], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, '-m', 'goose_research', '--data-dir', str(self.base), 'template'], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('操作失败', result.stdout.decode('utf-8'))

    def test_cli(self):
        self.assertEqual(main(['--data-dir', str(self.base), 'template']), 0)
        self.assertEqual(main(['--data-dir', str(self.base), 'template']), 1)
        self.assertEqual(main(['--data-dir', str(self.base), 'import-csv', str(self.csv)]), 0)
        snap = next((self.base / 'snapshots').glob('*.json'))
        self.assertEqual(main(['--data-dir', str(self.base), 'report', str(snap)]), 0)


if __name__ == '__main__':
    unittest.main()
