"""Offline integration only: all accounts, bars and encoded streams are fictional.

The optional group runs the real pinned decoder/API in an actual no-Git child
process. Only HTTP transport is replaced; this is not another live acceptance.
"""
import copy
from datetime import date, datetime, timezone
from http.client import RemoteDisconnected
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import holdings, market, report, sina, storage
from goose_research.usage import market_usage
from goose_research.validation import validate_market

NOW = datetime(2025, 1, 6, 9, tzinfo=timezone.utc)
# Artificial K2 stream: same fictional three bars as the decoder grammar tests.
BODY = b'var KLC_K2_sh515450="K2/HCMRCDcjCUQBIDAAAAAFogCoAAAAAAKQBFw+/AAYA";\n/* public test fixture */\n'
ROWS = [dict(time=d, open=o, high=h, low=lo, close=c, volume=v) for d, o, h, lo, c, v in (
    ('2025-01-02', '1.1', '1.2', '1', '1.15', '100'),
    ('2025-01-03', '1.15', '1.25', '1.05', '1.2', '110'),
    ('2025-01-06', '1.2', '1.3', '1.1', '1.25', '100'))]


def primary():
    return io.BytesIO(json.dumps(dict(data=dict(code='515450', market=1,
        klines=['2025-01-06,1.2,1.25,1.3,1.1,1']))).encode())


def disconnected(*_args, **_kwargs):
    raise RemoteDisconnected('FICTIONAL_PRIVATE_EXCEPTION_MUST_NOT_ESCAPE')


def candidate():
    return dict(rows=copy.deepcopy(ROWS), requested_url=sina.URL, raw_start='2025-01-02',
        raw_end='2025-01-06', raw_count=3, response_sha256='b' * 64, decoder='akshare-1.19.1',
        encoding=dict(wrapper='KLC_K2_sh515450', codec=3466, version=0, response_bytes=len(BODY)),
        volume_unit=sina.VOLUME_UNIT, api_rows_match=True)


def installed():
    try:
        sina._check_runtime_dependencies()
        return True
    except sina.SinaDependencyError:
        return False


class EnabledPolicyTests(unittest.TestCase):
    def test_default_gate_primary_priority_and_scoped_fallback(self):
        self.assertIs(market.SINA_DAILY_OBSERVATION_ENABLED, True)
        secondary = Mock(return_value=candidate())
        success = market.fetch('515450.SH', 'daily', lambda *_a, **_k: primary(),
                               now=NOW, sina_fetcher=secondary)
        secondary.assert_not_called()
        self.assertEqual(success['source'], '东方财富公开接口')
        fallback = market.fetch('515450.SH', 'daily', disconnected, now=NOW, sina_fetcher=secondary)
        secondary.assert_called_once_with('515450.SH', date(2022, 1, 6), date(2025, 1, 6))
        self.assertEqual(fallback['rows'], ROWS)  # raw volume unchanged, not *100
        self.assertEqual(market_usage(fallback), dict(raw_observation=True,
            historical_cost_reference=False, position_valuation=False,
            volume_analysis=False, investment_advice=False))
        self.assertEqual(fallback['provenance']['usage'], 'raw_observation_only')

    def test_missing_false_or_forged_api_agreement_is_unavailable(self):
        for value in (None, False, 1, 'true'):
            raw = candidate()
            if value is None:
                del raw['api_rows_match']
            else:
                raw['api_rows_match'] = value
            with self.subTest(value=value):
                result = market.fetch('515450.SH', 'daily', disconnected, now=NOW,
                                      sina_fetcher=lambda *_a: raw)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['rows'], [])
                self.assertFalse(any(market_usage(result).values()))

    def test_usage_flags_cannot_be_promoted_and_old_records_stay_restricted(self):
        result = market.fetch('515450.SH', 'daily', disconnected, now=NOW,
                              sina_fetcher=lambda *_a: candidate())
        for key, value in [('price_unit_verified', True), ('volume_unit_verified', True),
                           ('adjustment_verified', True), ('usage', 'valuation'), ('api_rows_match', False)]:
            changed = copy.deepcopy(result)
            changed['provenance'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_market(changed)
        missing = copy.deepcopy(result)
        del missing['provenance']['usage']
        with self.assertRaises(ValueError):
            validate_market(missing)
        for key in ('api_rows_match', 'usage', 'price_unit_verified', 'volume_unit_verified'):
            result['provenance'].pop(key)
        self.assertTrue(market_usage(result)['raw_observation'])
        self.assertFalse(market_usage(result)['historical_cost_reference'])


@unittest.skipUnless(installed(), 'requires existing clean pinned Sina tools; CI runs this group')
class RealWorkerIntegrationTests(unittest.TestCase):
    def runner(self, command, **kwargs):
        # Real child, real decoder, real API, real protocol; offline transport.
        # No Git dependency and a Windows legacy console codepage environment.
        program = '''
import socket
import runpy
import sys
from types import SimpleNamespace
from goose_research import sina
BODY = BODY_LITERAL
class Response:
    status = 200
    headers = {'Content-Length': str(len(BODY))}
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def geturl(self): return sina.URL
    def read(self, limit): return BODY[:limit]
sina.build_opener = lambda *_args: SimpleNamespace(open=lambda *_a, **_k: Response())
original_connect = socket.socket.connect
def no_network(sock, address):
    # Windows asyncio (used by mini-racer 0.14.1) creates its wakeup pipe
    # using a loopback socketpair. Preserve that OS IPC, forbid outbound I/O.
    if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'):
        return original_connect(sock, address)
    raise AssertionError('offline integration forbids network')
socket.socket.connect = no_network
# Exercise the Windows IPC explicitly; Linux's old runtime does not need it.
if sys.platform == 'win32':
    left, right = socket._fallback_socketpair()
    left.close()
    right.close()
try:
    no_network(SimpleNamespace(family=socket.AF_INET), ('192.0.2.1', 443))
except AssertionError:
    pass
else:
    raise AssertionError('outbound test guard did not reject the connection')
sys.argv = ['goose_research.sina'] + WORKER_ARGS
runpy.run_module('goose_research.sina', run_name='__main__')
'''.replace('BODY_LITERAL', repr(self.body)).replace('WORKER_ARGS', repr(command[3:]))
        return subprocess.run([sys.executable, '-c', program],
            env=dict(os.environ, PATH='', PYTHONIOENCODING='cp1252'), **kwargs)

    def fetcher(self, symbol, begin, end):
        return sina.fetch_sina_rows(symbol, begin, end, runner=self.runner)

    def setUp(self):
        self.body = BODY

    def test_real_worker_csv_collect_report_and_immutable_history(self):
        with tempfile.TemporaryDirectory() as temp:
            base = storage.initialize(Path(temp) / '本地虚构数据', True)
            csv = base / 'holdings.csv'
            csv.write_text(','.join(holdings.FIELDS) + '\n'
                'HB_TEST,华宝虚构,2025-01-06,partial,,515450.SH,etf,1000,800,\n'
                'YH_TEST,银河虚构,2025-01-06,complete,100,515450.SH,etf,200,0,1\n', encoding='utf-8-sig')
            snap = holdings.import_csv(csv, base)
            before = snap.read_bytes()
            original = market.fetch
            def fetch(*args, **kwargs):
                return original(*args, **kwargs, opener=disconnected, now=NOW, sina_fetcher=self.fetcher)
            with patch.object(market, 'fetch', side_effect=fetch):
                first = market.collect(base, '515450.SH', 'daily')
                old = first.read_bytes()
                second = market.collect(base, '515450.SH', 'daily')
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), old)
            self.assertEqual(snap.read_bytes(), before)
            document = json.loads(old)
            self.assertEqual(document['status'], 'observed')
            self.assertEqual(document['rows'], ROWS)
            self.assertEqual(document['provenance']['raw_count'], 3)
            self.assertTrue(document['provenance']['api_rows_match'])
            self.assertEqual([a['status'] for a in document['provider_attempts']], ['unavailable', 'observed'])
            with patch.object(report, 'scenario', side_effect=AssertionError('Sina must not price a scenario')):
                html_path = report.generate(base, snap, [first])
            html = html_path.read_text(encoding='utf-8')
            for text in ('华宝虚构', '银河虚构', '可用现金：未知', '总数量：1000',
                         '可卖数量（真实录入）：800', '仅原始日线观察', '复权口径尚未核验'):
                self.assertIn(text, html)
            self.assertNotIn('FICTIONAL_PRIVATE_EXCEPTION_MUST_NOT_ESCAPE', html)

    def test_actual_worker_api_parse_failure_is_closed_and_never_returns_partial_rows(self):
        # Validated wrapper, but original AKShare cannot parse this spacing.
        self.body = BODY.replace(b'="', b'= "')
        result = market.fetch('515450.SH', 'daily', disconnected, now=NOW, sina_fetcher=self.fetcher)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['provider_attempts'][-1]['error_type'], 'SinaDecodeError')
        self.assertNotIn('provenance', result)

    def test_actual_worker_invalid_tail_fails_before_decode(self):
        for tail in (b'/* incomplete', b'/* complete */alert(1)'):
            self.body = BODY.split(b'/*')[0] + tail
            with self.subTest(tail=tail):
                result = market.fetch('515450.SH', 'daily', disconnected, now=NOW, sina_fetcher=self.fetcher)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['rows'], [])
                self.assertEqual(result['provider_attempts'][-1]['error_type'], 'SinaResponseError')

    def test_actual_worker_old_data_cannot_pass_new_date_requirement(self):
        result = market.fetch('515450.SH', 'daily', disconnected,
            now=datetime(2025, 1, 7, 9, tzinfo=timezone.utc), sina_fetcher=self.fetcher)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['provider_attempts'][-1]['error_type'], 'StaleDailyData')


if __name__ == '__main__':
    unittest.main()
