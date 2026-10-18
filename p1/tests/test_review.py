"""Regression tests for review of 2b19af5; fictional inputs only."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import storage
from goose_research.report import render, generate


def snapshot():
    return dict(schema_version=1, as_of='2026-01-05', transaction_history_complete=False,
                accounts=[dict(account='FAKE', broker='虚构', coverage='partial', cash='12345.67', positions=[
                    dict(symbol='600900.SH', kind='stock', quantity=200, available=100,
                         broker_cost='10', cost_basis='broker_display')])])


def market():
    rows = [dict(time=d, open='10', close='11', high='12', low='9', volume='100') for d in ('2026-01-05', '2026-01-06')]
    return dict(schema_version=1, symbol='600900.SH', interval='daily', source='虚构测试源', adjustment='不复权',
                fetched_at='2026-01-07T00:00:00+00:00', requested_start='2026-01-01', requested_end='2026-01-07',
                status='observed', rows=rows, count=2, observed_start=rows[0]['time'], observed_end=rows[-1]['time'], stale=True)


class ReportReviewTests(unittest.TestCase):
    def test_reject_snapshot_version(self):
        doc = snapshot()
        doc['schema_version'] = 999
        with self.assertRaises(ValueError):
            render(doc, [])

    def test_reject_market_version(self):
        doc = market()
        doc['schema_version'] = 999
        with self.assertRaises(ValueError):
            render(snapshot(), [doc])

    def test_reject_future_market(self):
        doc = market()
        doc['rows'][-1]['time'] = '2099-01-05'
        doc['observed_end'] = '2099-01-05'
        with self.assertRaises(ValueError):
            render(snapshot(), [doc])

    def test_reject_unsorted_market(self):
        doc = market()
        doc['rows'].reverse()
        doc['observed_start'], doc['observed_end'] = doc['rows'][0]['time'], doc['rows'][-1]['time']
        with self.assertRaises(ValueError):
            render(snapshot(), [doc])

    def test_reject_invalid_price(self):
        doc = market()
        doc['rows'][-1]['close'] = '13'  # exceeds high=12 but old report still estimated costs
        with self.assertRaises(ValueError):
            render(snapshot(), [doc])

    def test_account_cash_and_cash_only(self):
        doc = snapshot()
        doc['accounts'].append(dict(account='CASH', broker='虚构', coverage='complete', cash='54321.00', positions=[]))
        html = render(doc, [])
        self.assertIn('可用现金：12345.67 元', html)
        self.assertIn('可用现金：54321.00 元', html)
        self.assertIn('无持仓（仅现金账户）', html)
        doc['accounts'][0]['cash'] = None
        self.assertIn('可用现金：未知', render(doc, []))

    def test_position_facts_distinct_from_scenarios(self):
        html = render(snapshot(), [market()])
        for label in ('资产类型：股票', '总数量：200', '可卖数量（真实录入）：100', '券商显示成本：10', '快照日期：2026-01-05', '未记录日内时刻', '软件估算情景：100股/份'):
            self.assertIn(label, html)

    def test_direct_unc_rejected_before_access(self):
        with self.assertRaises(ValueError):
            storage.local_path(r'\\server\share\中文私有目录')

class SharedValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = storage.initialize(Path(self.tmp.name) / '中文私有目录', True)

    def test_snapshot_invariants(self):
        invalid = []
        for key, value in [('as_of', '2099-01-05'), ('schema_version', True), ('accounts', {}), ('transaction_history_complete', True)]:
            d = snapshot()
            d[key] = value
            invalid.append(d)
        for key, value in [('quantity', -1), ('quantity', True), ('available', 201), ('broker_cost', 'NaN'), ('symbol', '600900'), ('kind', 'bond')]:
            d = snapshot()
            d['accounts'][0]['positions'][0][key] = value
            invalid.append(d)
        d = snapshot()
        d['accounts'].append(copy.deepcopy(d['accounts'][0]))
        invalid.append(d)
        d = snapshot()
        d['accounts'][0]['positions'] *= 2
        invalid.append(d)
        d = snapshot()
        d['accounts'][0]['cash'] = None
        d['accounts'][0]['coverage'] = 'complete'
        invalid.append(d)
        for d in invalid:
            with self.subTest(document=invalid.index(d)), self.assertRaises(ValueError):
                render(d, [])

    def test_market_invariants(self):
        invalid = []
        for key, value in [('interval', 'tick'), ('symbol', 'bad'), ('status', 'ready'), ('fetched_at', '2099-01-05T00:00:00+00:00'), ('fetched_at', '2026-01-07T00:00:00'), ('count', 999), ('observed_start', '2026-01-04'), ('stale', False), ('requested_start', '2026-01-07')]:
            d = market()
            d[key] = value
            invalid.append(d)
        for key, value in [('close', 'NaN'), ('close', 'Infinity'), ('close', '0'), ('low', '-1'), ('volume', '-1'), ('close', '1e999999'), ('time', '2099-01-05')]:
            d = market()
            d['rows'][-1][key] = value
            invalid.append(d)
        d = market()
        d['rows'][1] = copy.deepcopy(d['rows'][0])
        invalid.append(d)
        d = market()
        d['status'] = 'unavailable'  # cannot hide present bars behind unavailable
        invalid.append(d)
        d = market()
        d['interval'] = 'minute'
        d['rows'][0]['time'], d['rows'][1]['time'] = '2026-01-05 09:31', '2099-01-05 09:32'
        invalid.append(d)
        for i, d in enumerate(invalid):
            with self.subTest(case=i), self.assertRaises(ValueError):
                render(snapshot(), [d])

    def test_file_boundary_and_no_output_on_error(self):
        snap = self.base / 'snapshot.json'
        snap.write_text(json.dumps(snapshot()), encoding='utf-8')
        bad = market()
        bad['schema_version'] = 999
        quotes = self.base / 'quotes.json'
        quotes.write_text(json.dumps(bad), encoding='utf-8')
        with self.assertRaises(ValueError):
            storage.read_private(quotes, self.base)
        with self.assertRaises(ValueError):
            generate(self.base, snap, [quotes])
        self.assertFalse((self.base / 'reports').exists())
        for raw in ('[]', '{bad SECRET_DO_NOT_ECHO', '{"schema_version":1,"schema_version":999}', '{"value":NaN}'):
            quotes.write_text(raw, encoding='utf-8')
            with self.assertRaises(ValueError) as exc:
                storage.read_private(quotes, self.base)
            self.assertNotIn('SECRET_DO_NOT_ECHO', str(exc.exception))
        with self.assertRaises(ValueError):
            storage.save(self.base, 'snapshots', bad)

    def test_unavailable_and_etf_unknown_cost(self):
        from goose_research.market import fetch
        def fail(*a, **k):
            raise OSError('SECRET_DO_NOT_ECHO')
        unavailable = fetch('600900.SH', 'daily', fail)
        html = render(snapshot(), [unavailable])
        self.assertIn('不可用', html)
        self.assertNotIn('往返费用', html)
        self.assertNotIn('SECRET_DO_NOT_ECHO', html)
        empty = snapshot()
        empty['accounts'][0]['positions'] = []
        self.assertIn('尚无持仓录入，不代表账户实际空仓', render(empty, []))
        d = snapshot()
        p = d['accounts'][0]['positions'][0]
        p['kind'], p['symbol'], p['broker_cost'] = 'etf', '515450.SH', None
        html = render(d, [])
        self.assertIn('资产类型：ETF', html)
        self.assertIn('券商显示成本：未知', html)

    def test_bad_links_fail_closed(self):
        link = self.base / 'broken'
        try:
            link.symlink_to(self.base / 'missing', target_is_directory=True)
        except OSError:
            self.skipTest('OS denies symlink creation')
        with self.assertRaises(ValueError):
            storage.local_path(link / 'child')

    def test_local_names_not_confused_with_cloud_names(self):
        for name in ('tmpbox123', '中文box笔记', '本地资料'):
            self.assertEqual(storage.local_path(self.base / name), self.base / name)
        for name in ('Box', 'OneDrive - Example', 'Dropbox (Personal)'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                storage.local_path(self.base / name)

    def test_windows_drive_policy_on_all_platforms(self):
        # Deterministic Win32 boundary tests; no real network is contacted.
        for drive_type in (0, 1, 2, 4, 5, 6):
            with self.subTest(drive_type=drive_type), patch.object(storage, '_drive_type', return_value=drive_type), self.assertRaises(ValueError):
                storage._check_windows_path(r'Z:\中文私有目录')
        with patch.object(storage, '_drive_type', return_value=3) as api:
            storage._check_windows_path(r'C:\中文私有目录')
            api.assert_called_once_with('C:\\')
        for value in (r'\\server\share\中文', r'\\?\UNC\server\share\中文', r'\\.\C:\中文', r'C:中文'):
            with self.subTest(value=value), patch.object(storage, '_drive_type') as api, self.assertRaises(ValueError):
                storage._check_windows_path(value)
            api.assert_not_called()
        with patch.object(storage, '_drive_type', side_effect=OSError), self.assertRaises(ValueError):
            storage._check_windows_path(r'C:\中文')


@unittest.skipUnless(os.name == 'nt', 'Windows native storage tests')
class WindowsStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_native_fixed_drive_chinese_directory(self):
        p = self.base / '中文私有目录' / '新建子目录'
        self.assertEqual(storage.local_path(p), p.resolve())
        self.assertTrue(storage.initialize(p, True).is_dir())

    def test_unc_never_resolved(self):
        with patch.object(storage, '_resolve_target') as resolver:
            for p in (r'\\server\share\中文', r'\\?\UNC\server\share\中文'):
                with self.assertRaises(ValueError):
                    storage.local_path(p)
            resolver.assert_not_called()

    def test_mapped_drive_rejected_before_resolution(self):
        with patch.object(storage, '_drive_type', return_value=4), patch.object(storage, '_resolve_target') as resolver:
            with self.assertRaises(ValueError):
                storage.local_path(r'Z:\中文私有目录')
            resolver.assert_not_called()

    def test_resolved_link_network_target(self):
        for target in (Path(r'\\server\share\中文'), Path(r'Z:\中文')):
            def drive(anchor):
                return 4 if anchor == 'Z:\\' else 3
            with self.subTest(target=str(target)), patch.object(storage, '_resolve_target', return_value=target), patch.object(storage, '_drive_type', side_effect=drive), self.assertRaises(ValueError):
                storage.local_path(self.base / 'link' / 'child')

    def test_real_junction_local_and_repository(self):
        from goose_research.storage import REPO
        target = self.base / '中文本地目标'
        target.mkdir()
        link = self.base / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
        self.assertEqual(result.returncode, 0, 'CI must support ordinary directory junctions')
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        self.assertEqual(storage.local_path(link / 'new'), target / 'new')
        forbidden = self.base / 'repo-junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(forbidden), str(REPO)], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.addCleanup(lambda: forbidden.rmdir() if forbidden.exists() else None)
        with self.assertRaises(ValueError):
            storage.local_path(forbidden / 'private')

    def test_unknown_drive_rejected(self):
        with patch.object(storage, '_drive_type', return_value=0), self.assertRaises(ValueError):
            storage.local_path(self.base / '中文')


if __name__ == '__main__':
    unittest.main()
