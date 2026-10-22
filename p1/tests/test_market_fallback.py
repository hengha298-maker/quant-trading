"""Provider fallback regressions: fictional injected responses; no real networking.

Passing these tests does not verify Sina availability, adjustment semantics,
volume units, exchange calendars, broker fees, or an investment strategy.
"""
import copy
from datetime import date, datetime, timezone, timedelta
from http.client import RemoteDisconnected
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import market, report, sina, storage
from goose_research.validation import validate_market

BJ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 19, 17, tzinfo=BJ)
SENSITIVE = 'PRIVATE_REMOTE_BODY_NOT_FOR_REPORTS'


def bar(day='2026-10-19', close='1.02'):
    return dict(time=day, open='1.01', close=close, high='1.04', low='1.00', volume='100')


def primary(rows=None, code='515450', market_id=1):
    rows = [bar()] if rows is None else rows
    lines = [','.join(str(row[key]) for key in ('time', 'open', 'close', 'high', 'low', 'volume')) for row in rows]
    body = json.dumps(dict(data=dict(code=code, market=market_id, klines=lines))).encode('utf-8')
    return Mock(side_effect=lambda *args, **kwargs: io.BytesIO(body))


def disconnected(*args, **kwargs):
    raise RemoteDisconnected(SENSITIVE)


def candidate(rows=None):
    rows = [bar(close='1.03')] if rows is None else rows
    return dict(rows=copy.deepcopy(rows), requested_url=sina.URL,
                raw_start='2026-10-16', raw_end='2026-10-19', raw_count=max(2, len(rows)),
                response_sha256='a' * 64, decoder='akshare-1.19.1',
                volume_unit='源站原始值（AKShare文档称手，未核验）')


def snapshot():
    return dict(schema_version=1, as_of='2026-10-19', transaction_history_complete=False,
                accounts=[dict(account='FICTIONAL_ONLY', broker='虚构测试账户', coverage='partial',
                               cash='10000', positions=[dict(symbol='515450.SH', kind='etf',
                               quantity=2000, available=1000, broker_cost=None, cost_basis='broker_display')])])


class FallbackTests(unittest.TestCase):
    def setUp(self):
        # Real availability has not been verified. Only tests override the gate.
        self.gate = patch.object(market, 'SINA_DAILY_VERIFIED', True)
        self.gate.start()
        self.addCleanup(self.gate.stop)
        self.secondary = Mock(side_effect=lambda *args: candidate())

    def fetch(self, opener=None, **kwargs):
        return market.fetch('515450.SH', 'daily', primary() if opener is None else opener,
                            now=kwargs.pop('now', NOW), sina_fetcher=kwargs.pop('sina_fetcher', self.secondary), **kwargs)

    def assert_unavailable(self, document):
        self.assertEqual(document['status'], 'unavailable')
        self.assertEqual(document['rows'], [])
        for key in ('observed_start', 'observed_end', 'count', 'stale', 'audit_detail', 'freshness', 'provenance'):
            self.assertNotIn(key, document)
        self.assertNotIn(SENSITIVE, json.dumps(document))
        self.assertIs(validate_market(document), document)

    def test_primary_success_does_not_call_secondary(self):
        result = self.fetch()
        self.assertEqual(result['source'], '东方财富公开接口')
        self.assertEqual(result['rows'], [bar()])
        self.assertEqual(result['provider_attempts'], [dict(provider='eastmoney', status='observed')])
        self.secondary.assert_not_called()

    def test_disconnect_falls_back_to_one_unverified_adjustment_source(self):
        result = self.fetch(disconnected)
        self.assertEqual(result['source'], market.SINA_SOURCE)
        self.assertEqual(result['adjustment'], market.SINA_ADJUSTMENT)
        self.assertEqual(result['rows'], candidate()['rows'])
        self.assertEqual(result['provider_attempts'], [
            dict(provider='eastmoney', status='unavailable', error_type='RemoteDisconnected'),
            dict(provider='sina', status='observed')])
        self.assertEqual(result['provenance']['symbol'], 'sh515450')
        self.assertFalse(result['provenance']['adjustment_verified'])
        self.secondary.assert_called_once_with('515450.SH', date(2023, 10, 19), date(2026, 10, 19))
        self.assertNotIn(SENSITIVE, json.dumps(result))

    def test_invalid_primary_batches_are_discarded_before_fallback(self):
        cases = {
            'wrong_code': primary(code='600900'),
            'wrong_market': primary(market_id=0),
            'nan': primary([dict(bar(), close='NaN')]),
            'bad_ohlc': primary([dict(bar(), close='2')]),
            'duplicate': primary([bar(), bar()]),
            'descending': primary([bar(), bar('2026-10-16')]),
            'outside_range': primary([bar('2020-01-02')]),
            'stale': primary([bar('2026-10-16')]),
            'empty': primary([]),
            'partial_then_invalid': primary([bar('2026-10-16', '1.01'), dict(bar(), close='NaN')]),
        }
        for name, opener in cases.items():
            with self.subTest(case=name):
                result = self.fetch(opener)
                self.assertEqual(result['status'], 'observed')
                self.assertEqual(result['source'], market.SINA_SOURCE)
                self.assertEqual(result['rows'], candidate()['rows'])
                self.assertEqual(result['provider_attempts'][0]['status'], 'unavailable')
                self.assertEqual(result['count'], 1)
        self.assertEqual(self.secondary.call_count, len(cases))

    def test_double_failure_clears_success_metadata_and_exception_text(self):
        partial = primary([bar('2026-10-16'), dict(bar(), close='NaN')])
        result = self.fetch(partial, sina_fetcher=Mock(side_effect=RuntimeError(SENSITIVE)))
        self.assert_unavailable(result)
        self.assertEqual(result['error_type'], 'AllSourcesUnavailable')
        self.assertEqual(result['provider_attempts'][-1],
                         dict(provider='sina', status='unavailable', error_type='RuntimeError'))

    def test_invalid_secondary_never_becomes_observed(self):
        cases = {
            'nan': [dict(bar(), close='NaN')],
            'bad_ohlc': [dict(bar(), low='2')],
            'duplicate': [bar(), bar()],
            'descending': [bar(), bar('2026-10-16')],
            'outside_range': [bar('2020-01-02')],
            'stale': [bar('2026-10-16')],
            'empty': [],
            'future': [bar('2099-01-01')],
            'negative_volume': [dict(bar(), volume='-1')],
        }
        for name, rows in cases.items():
            with self.subTest(case=name):
                result = self.fetch(disconnected, sina_fetcher=Mock(return_value=candidate(rows)))
                self.assert_unavailable(result)
                self.assertEqual(result['error_type'], 'AllSourcesUnavailable')
                self.assertEqual(result['provider_attempts'][-1]['status'], 'unavailable')

    def test_default_unverified_gate_skips_without_calling_candidate(self):
        with patch.object(market, 'SINA_DAILY_VERIFIED', False):
            result = self.fetch(disconnected)
        self.assert_unavailable(result)
        self.assertEqual(result['error_type'], 'SourceNotVerified')
        self.assertEqual(result['provider_attempts'][-1],
                         dict(provider='sina', status='skipped', error_type='SourceNotVerified'))
        self.secondary.assert_not_called()

    def test_forced_unverified_candidate_is_also_skipped(self):
        opener = Mock(side_effect=AssertionError('primary should not run'))
        with patch.object(market, 'SINA_DAILY_VERIFIED', False):
            result = self.fetch(opener, source='sina')
        self.assert_unavailable(result)
        self.assertEqual(result['provider_attempts'],
                         [dict(provider='sina', status='skipped', error_type='SourceNotVerified')])
        self.secondary.assert_not_called()
        opener.assert_not_called()

    def test_forced_candidate_does_not_request_primary(self):
        opener = Mock(side_effect=AssertionError('primary should not run'))
        result = self.fetch(opener, source='sina')
        self.assertEqual(result['provider_attempts'], [dict(provider='sina', status='observed')])
        self.assertEqual(result['status'], 'observed')
        opener.assert_not_called()

    def test_forced_primary_does_not_request_candidate(self):
        result = self.fetch(disconnected, source='eastmoney')
        self.assert_unavailable(result)
        self.assertEqual(result['provider_attempts'],
                         [dict(provider='eastmoney', status='unavailable', error_type='RemoteDisconnected')])
        self.secondary.assert_not_called()

    def test_auto_fallback_is_scoped_to_only_515450_daily(self):
        for symbol, interval in [('600900.SH', 'daily'), ('515450.SH', 'minute'), ('600900.SH', 'minute')]:
            with self.subTest(symbol=symbol, interval=interval):
                result = market.fetch(symbol, interval, disconnected, now=NOW, sina_fetcher=self.secondary)
                self.assert_unavailable(result)
                self.assertEqual([a['provider'] for a in result['provider_attempts']], ['eastmoney'])
        self.secondary.assert_not_called()

    def test_invalid_source_or_explicit_candidate_scope_is_rejected(self):
        for source in ('other', None, ''):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.fetch(source=source)
        for symbol, interval in [('600900.SH', 'daily'), ('515450.SH', 'minute')]:
            with self.subTest(symbol=symbol, interval=interval), self.assertRaises(ValueError):
                market.fetch(symbol, interval, disconnected, source='sina', now=NOW, sina_fetcher=self.secondary)
        self.secondary.assert_not_called()

    def test_naive_clock_and_minute_date_override_are_rejected(self):
        with self.assertRaises(ValueError):
            self.fetch(now=NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            market.fetch('515450.SH', 'minute', disconnected, now=NOW, expected_last_date='2026-10-16')

    def test_before_1600_requests_previous_weekday_without_calendar_claim(self):
        opener = primary([bar('2026-10-16')])
        result = self.fetch(opener, source='eastmoney', now=NOW.replace(hour=15, minute=59))
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['requested_end'], '2026-10-16')
        self.assertEqual(parse_qs(urlsplit(opener.call_args.args[0]).query)['end'], ['20261016'])
        self.assertEqual(result['freshness']['basis'], 'conservative_weekday_not_exchange_calendar')
        self.assertFalse(result['freshness']['calendar_verified'])
        self.assertTrue(result['stale'])

    def test_unfinished_current_daily_bar_is_rejected_before_fallback(self):
        result = self.fetch(primary(), now=NOW.replace(hour=15),
                            sina_fetcher=Mock(return_value=candidate([bar('2026-10-16')])))
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['rows'][0]['time'], '2026-10-16')
        self.assertEqual(result['provider_attempts'][0]['status'], 'unavailable')

    def test_1600_boundary_and_weekend_use_explicit_date_semantics(self):
        at_close = self.fetch(source='eastmoney', now=NOW.replace(hour=16, minute=0))
        self.assertEqual(at_close['requested_end'], '2026-10-19')
        sunday = datetime(2026, 10, 18, 17, tzinfo=BJ)
        weekend = self.fetch(primary([bar('2026-10-16')]), source='eastmoney', now=sunday)
        self.assertEqual(weekend['requested_end'], '2026-10-16')
        self.assertEqual(weekend['status'], 'observed')
        self.assertFalse(weekend['audit_detail']['calendar_verified'])

    def test_holiday_candidate_rejects_stale_unless_last_session_is_explicit(self):
        holiday = datetime(2026, 10, 1, 17, tzinfo=BJ)
        opener = primary([bar('2026-09-30')])
        rejected = self.fetch(opener, source='eastmoney', now=holiday)
        self.assert_unavailable(rejected)
        self.assertEqual(rejected['error_type'], 'StaleDailyData')
        confirmed = self.fetch(opener, source='eastmoney', now=holiday, expected_last_date='2026-09-30')
        self.assertEqual(confirmed['status'], 'observed')
        self.assertEqual(confirmed['freshness']['basis'], 'user_confirmed_date_not_calendar_verification')
        self.assertFalse(confirmed['freshness']['calendar_verified'])

    def test_future_or_unfinished_explicit_date_never_calls_providers(self):
        opener = Mock(side_effect=AssertionError('must not run'))
        for now, target in [(NOW, '2099-01-01'), (NOW.replace(hour=15), '2026-10-19')]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.fetch(opener, now=now, expected_last_date=target)
        opener.assert_not_called()
        self.secondary.assert_not_called()

    def test_missing_optional_dependency_fails_closed_through_worker_boundary(self):
        def runner(command, **kwargs):
            self.assertEqual(command[1:4], ['-m', 'goose_research.sina', '--worker'])
            self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
            self.assertIs(kwargs['stderr'], subprocess.DEVNULL)
            kwargs['stdout'].write(b'{"error":"dependency"}')
            return SimpleNamespace(returncode=1)
        def fetcher(symbol, begin, end):
            return sina.fetch_sina_rows(symbol, begin, end, runner=runner)
        result = self.fetch(disconnected, sina_fetcher=fetcher)
        self.assert_unavailable(result)
        self.assertEqual(result['provider_attempts'][-1],
                         dict(provider='sina', status='unavailable', error_type='SinaDependencyError'))


class ProvenanceAndReportTests(unittest.TestCase):
    def observed(self):
        with patch.object(market, 'SINA_DAILY_VERIFIED', True):
            return market.fetch('515450.SH', 'daily', disconnected, now=NOW,
                                sina_fetcher=Mock(return_value=candidate()))

    def test_provenance_and_freshness_tampering_are_rejected(self):
        changes = [
            ('source', '东方财富公开接口'), ('symbol', '600900.SH'),
            ('adjustment', '不复权'), ('count', 9),
            ('provenance.provider', 'eastmoney'), ('provenance.symbol', 'sh600900'),
            ('provenance.response_sha256', 'not-a-hash'), ('provenance.decoder', 'latest'),
            ('provenance.raw_count', 0), ('provenance.raw_count', True),
            ('provenance.raw_start', '2026-10-20'), ('provenance.raw_end', '2099-01-01'),
            ('provenance.volume_unit', 'shares'), ('provenance.adjustment_verified', True),
            ('provenance.raw_order_verified', False), ('freshness.checked_at', '2026-10-18T09:00:00+00:00'),
            ('freshness.latest_date', '2026-10-16'), ('freshness.calendar_verified', True),
            ('freshness.state', 'live'), ('freshness.basis', 'exchange_verified'),
        ]
        original = self.observed()
        for dotted, value in changes:
            with self.subTest(field=dotted):
                doc = copy.deepcopy(original)
                parts = dotted.split('.')
                target = doc if len(parts) == 1 else doc[parts[0]]
                target[parts[-1]] = value
                with self.assertRaises(ValueError):
                    validate_market(doc)

    def test_raw_count_must_match_range_and_single_row_semantics(self):
        original = self.observed()
        for changes in [dict(raw_count=5), dict(raw_count=1), dict(raw_start='2026-10-19')]:
            with self.subTest(changes=changes):
                doc = copy.deepcopy(original)
                doc['provenance'].update(changes)
                with self.assertRaises(ValueError):
                    validate_market(doc)

    def test_conservative_expected_date_cannot_be_backdated_to_hide_staleness(self):
        doc = self.observed()
        doc['freshness']['expected_last_date'] = '2026-10-16'
        with self.assertRaises(ValueError):
            validate_market(doc)

    def test_explicit_metadata_cannot_accept_unfinished_current_daily_bar(self):
        doc = self.observed()
        doc['fetched_at'] = '2026-10-19T07:00:00+00:00'  # 15:00 Beijing
        doc['freshness'].update(checked_at=doc['fetched_at'], basis='user_confirmed_date_not_calendar_verification')
        with self.assertRaises(ValueError):
            validate_market(doc)

    def test_candidate_cannot_be_relabelled_unadjusted_by_removing_provenance(self):
        doc = self.observed()
        doc['adjustment'] = '不复权'
        del doc['provenance']
        with self.assertRaises(ValueError):
            validate_market(doc)

    def test_candidate_attempt_cannot_be_relabelled_as_primary_without_provenance(self):
        doc = self.observed()
        doc['source'] = '东方财富公开接口'
        doc['adjustment'] = '不复权'
        del doc['provenance']
        with self.assertRaises(ValueError):
            validate_market(doc)

    def test_candidate_url_tampering_is_rejected(self):
        doc = self.observed()
        doc['url'] = 'https://example.invalid/different-security'
        with self.assertRaises(ValueError):
            validate_market(doc)

    def test_historical_schema_without_new_metadata_still_reads(self):
        old = dict(schema_version=1, symbol='515450.SH', interval='daily', source='东方财富公开接口',
                   adjustment='不复权', fetched_at='2026-10-19T09:00:00+00:00',
                   requested_start='2026-10-16', requested_end='2026-10-19', status='observed',
                   rows=[bar('2026-10-16'), bar()], count=2,
                   observed_start='2026-10-16', observed_end='2026-10-19', stale=False)
        self.assertIs(validate_market(old), old)
        with tempfile.TemporaryDirectory() as directory:
            base = storage.initialize(Path(directory) / 'local-private', True)
            saved = storage.save(base, 'market', old)
            self.assertEqual(storage.read_private(saved, base, 'market'), old)

    def test_unknown_adjustment_displays_facts_but_never_calculates_fees(self):
        with patch.object(report, 'scenario', side_effect=AssertionError('fees must not run')) as fees:
            html = report.render(snapshot(), [self.observed()])
        fees.assert_not_called()
        self.assertIn('备用源复权口径尚未核验', html)
        self.assertIn('仅展示行情事实，不计算费用参考', html)
        self.assertIn('截至 2026-10-19', html)
        self.assertNotIn('往返费用 ', html)
        self.assertNotIn('PRIVATE_REMOTE_BODY', html)

    def test_report_age_uses_observation_date_instead_of_fetch_date(self):
        observation = market.fetch('515450.SH', 'daily', primary([bar('2026-10-16')]),
                                   source='eastmoney', now=NOW, expected_last_date='2026-10-16')
        class ReportClock(datetime):
            @classmethod
            def now(cls, tz=None):
                fixed = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
                return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)
        with patch.object(report, 'datetime', ReportClock):
            html = report.render(snapshot(), [observation])
        self.assertIn('截至 2026-10-16；距本次报告日期 6 自然日', html)
        self.assertIn('2026-10-19T09:00:00+00:00', html)
        self.assertIn('非实时', html)

    def test_two_saves_never_overwrite_snapshot_or_market_history(self):
        with tempfile.TemporaryDirectory() as directory:
            base = storage.initialize(Path(directory) / 'local-private', True)
            for category, doc in [('snapshots', snapshot()), ('market', self.observed())]:
                with self.subTest(category=category):
                    first = storage.save(base, category, doc)
                    before = first.read_bytes()
                    second = storage.save(base, category, doc)
                    self.assertNotEqual(first, second)
                    self.assertEqual(first.read_bytes(), before)
                    self.assertEqual(storage.read_private(first, base), doc)
                    self.assertEqual(storage.read_private(second, base), doc)


if __name__ == '__main__':
    unittest.main()
