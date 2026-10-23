"""Offline, explicitly fictional substitutes; never proof of live Sina service."""
import copy
from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import market, probe_sina as probe, sina


BODY = b'var KLC_KL_sh515450="HX/FICTIONAL+PAYLOAD/123";'
NOW = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)


def bars():
    return [dict(date=stamp, open=1.1, high=1.2, low=1.0, close=1.15, volume=100)
            for stamp in ('2025-01-02', '2025-01-03', '2025-01-06')]


class Response:
    status = 200
    headers = {}
    def __init__(self, body=BODY):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        pass
    def read(self, limit):
        return self.body[:limit]
    def geturl(self):
        return sina.URL


class Frame:
    def __init__(self, data):
        self.data = data
    def __len__(self):
        return len(self.data)
    def to_dict(self, orient):
        if orient != 'records':
            raise AssertionError('wrong API conversion')
        return copy.deepcopy(self.data)


def fake_api_runtime(mutate=None, wrong_url=False, body=BODY):
    """Shape-equivalent API test double: transport, decoder and sorted DataFrame."""
    transport = types.SimpleNamespace(get=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('unpatched transport')))
    class Engine:
        def eval(self, script, **kwargs):
            if script != 'LOCAL STATIC DECODER' or kwargs != {'timeout': 5000, 'max_memory': 128 * 1024 * 1024}:
                raise AssertionError('unbounded script')
        def call(self, name, payload, **kwargs):
            if name != 'd' or payload != body.decode().split('="')[1].split('"')[0] or kwargs != {'timeout': 5000, 'max_memory': 128 * 1024 * 1024}:
                raise AssertionError('unbounded payload')
            return bars()
    runtime = types.SimpleNamespace(MiniRacer=Engine)
    def api(symbol):
        if symbol != 'sh515450':
            raise AssertionError('wrong symbol')
        response = transport.get('https://example.invalid/' if wrong_url else sina.URL)
        if response.text != body.decode():
            raise AssertionError('original API must see exact downloaded response')
        engine = runtime.MiniRacer()
        engine.eval('LOCAL STATIC DECODER')
        result = engine.call('d', response.text.split('=')[1].split(';')[0].replace('"', ''))
        result = sorted(result, key=lambda row: row['date'])
        for row in result:
            row['date'] = date.fromisoformat(row['date'])
        if mutate:
            mutate(result)
        return Frame(result)
    return (api, transport, runtime), ('LOCAL STATIC DECODER', Engine)


def worker_summary(**kwargs):
    return probe._probe_worker('2022-01-06', '2025-01-06', probe.BASES[0],
                               opener=lambda *_a, **_k: Response(), decoder=lambda _s: bars(),
                               verifier=lambda _compressed, _raw: True, now=NOW, **kwargs)


class SinaProbeTests(unittest.TestCase):
    def test_original_api_shape_is_called_and_matches_every_raw_row(self):
        for body in (BODY, b' \nvar KLC_K2_sh515450="K2/FICTIONAL+PAYLOAD/123";\n'):
            api_parts, runtime = fake_api_runtime(body=body)
            with self.subTest(body=body), patch.object(probe, '_original_api', return_value=api_parts), patch.object(sina, '_optional_runtime', return_value=runtime):
                summary = probe._probe_worker('2022-01-06', '2025-01-06', probe.BASES[0],
                    opener=lambda *_a, **_k: Response(body), now=NOW)
            self.assertEqual(summary['api_checked'], 'akshare.fund_etf_hist_sina')
            self.assertIs(summary['api_rows_match'], True)
            self.assertEqual(summary['selected_count'], 3)
            self.assertEqual(summary['adjustment'], 'unknown')
            self.assertFalse(summary['gate_changed'])
            self.assertEqual(summary['fetched_at'], NOW.isoformat())

    def test_api_different_price_missing_row_or_changed_date_fails(self):
        for mutate in [lambda rows: rows[0].update(close=1.16), lambda rows: rows.pop(),
                       lambda rows: rows[0].update(date=date(2025, 1, 1))]:
            api_parts, runtime = fake_api_runtime(mutate)
            with self.subTest(mutate=mutate), patch.object(probe, '_original_api', return_value=api_parts), patch.object(sina, '_optional_runtime', return_value=runtime):
                with self.assertRaises(probe.SinaApiMismatchError):
                    probe._verify_original_api(sina._download(lambda *_a, **_k: Response()), sina._normalise_rows(bars()))
        # Dropping optional pandas columns must never admit missing/nonfinite
        # core OHLCV or turn an unknown required value into zero.
        for mutate in [lambda rows: rows[1].update(close=float('nan')),
                       lambda rows: rows[1].update(volume=None), lambda rows: rows[1].pop('open')]:
            api_parts, runtime = fake_api_runtime(mutate)
            with self.subTest(core_mutation=mutate), patch.object(probe, '_original_api', return_value=api_parts), patch.object(sina, '_optional_runtime', return_value=runtime):
                with self.assertRaises(sina.SinaError):
                    probe._verify_original_api(sina._download(lambda *_a, **_k: Response()), sina._normalise_rows(bars()))

    def test_original_api_cannot_fall_back_to_other_network_url(self):
        api_parts, runtime = fake_api_runtime(wrong_url=True)
        with patch.object(probe, '_original_api', return_value=api_parts), patch.object(sina, '_optional_runtime', return_value=runtime):
            with self.assertRaises(sina.SinaResponseError):
                probe._verify_original_api(sina._download(lambda *_a, **_k: Response()), sina._normalise_rows(bars()))

    def test_invalid_raw_order_fails_before_api_sorting(self):
        raw = bars(); raw.reverse()
        with self.assertRaises(sina.SinaDecodeError):
            probe._probe_worker('2022-01-06', '2025-01-06', probe.BASES[0], opener=lambda *_a, **_k: Response(),
                decoder=lambda _s: raw, verifier=lambda *_a: self.fail('API sorting cannot repair raw order'), now=NOW)

    def test_no_payload_stale_or_empty_data_cannot_succeed(self):
        def unavailable(*_a, **_k):
            raise sina.SinaResponseError()
        with self.assertRaises(sina.SinaResponseError):
            probe._probe_worker('2022-01-06', '2025-01-06', probe.BASES[0], opener=unavailable)
        for raw, error in [(bars()[:-1], probe.SinaStaleDataError), ([], sina.SinaDecodeError)]:
            with self.subTest(raw=raw), self.assertRaises(error):
                probe._probe_worker('2022-01-06', '2025-01-06', probe.BASES[0], opener=lambda *_a, **_k: Response(),
                    decoder=lambda _s: raw, verifier=lambda *_a: True, now=NOW)

    def test_default_date_uses_16_hour_weekday_candidate_explicit_is_preserved(self):
        for now, expected in [(NOW, '2025-01-06'), (NOW.replace(hour=7), '2025-01-03')]:
            summary = worker_summary(); summary['expected_last_date'] = expected; summary['selected_end'] = expected
            if expected == '2025-01-03':
                summary['selected_count'] = 2
                summary['last_bar'] = sina._normalise_rows(bars())[1]
            def runner(command, **kwargs):
                self.assertEqual(command[-2:], [expected, probe.BASES[0]])
                self.assertEqual(kwargs['timeout'], 45)
                self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
                kwargs['stdout'].write(json.dumps(summary).encode())
                return types.SimpleNamespace(returncode=0)
            self.assertEqual(probe.probe(runner=runner, now=now)['expected_last_date'], expected)
        summary = worker_summary(); summary['date_basis'] = probe.BASES[1]
        def explicit_runner(command, **kwargs):
            self.assertEqual(command[-2:], ['2025-01-06', probe.BASES[1]])
            kwargs['stdout'].write(json.dumps(summary).encode())
            return types.SimpleNamespace(returncode=0)
        self.assertEqual(probe.probe('2025-01-06', runner=explicit_runner, now=NOW)['date_basis'], probe.BASES[1])

    def test_outer_timeout_missing_dependency_and_mismatch_are_fixed_types(self):
        def timeout(*_a, **_k):
            raise subprocess.TimeoutExpired('private command', 45)
        with self.assertRaises(sina.SinaTimeoutError):
            probe.probe(runner=timeout, now=NOW)
        for code, error in [('dependency', sina.SinaDependencyError), ('api_mismatch', probe.SinaApiMismatchError),
                            ('stale', probe.SinaStaleDataError), ('encoding', sina.SinaEncodingError)]:
            def runner(_command, **kwargs):
                kwargs['stdout'].write(json.dumps({'error': code}).encode())
                return types.SimpleNamespace(returncode=1)
            with self.subTest(code=code), self.assertRaises(error):
                probe.probe(runner=runner, now=NOW)

    def test_original_api_missing_or_wrong_version_is_optional_dependency_failure(self):
        with patch.object(sina.metadata, 'version', return_value='999'):
            with self.assertRaises(sina.SinaDependencyError):
                probe._original_api()
        module = types.SimpleNamespace(fund_etf_hist_sina=None)
        with patch.object(sina.metadata, 'version', return_value='1.19.1'), patch.object(probe.importlib, 'import_module', return_value=module):
            with self.assertRaises(sina.SinaDependencyError):
                probe._original_api()

    def test_probe_does_not_read_holdings_save_or_modify_gate(self):
        self.assertIs(market.SINA_DAILY_OBSERVATION_ENABLED, True)
        summary = worker_summary()
        def runner(_command, **kwargs):
            kwargs['stdout'].write(json.dumps(summary).encode())
            return types.SimpleNamespace(returncode=0)
        with patch.object(market, 'save', side_effect=AssertionError('must not save')), patch.object(market, 'fetch', side_effect=AssertionError('must not collect')):
            result = probe.probe(runner=runner, now=NOW)
        self.assertFalse(result['gate_changed'])
        self.assertIs(market.SINA_DAILY_OBSERVATION_ENABLED, True)

    def test_outer_rejects_oversize_duplicate_malformed_and_false_success(self):
        invalid = worker_summary(); invalid['api_rows_match'] = False
        for raw in [b'x' * (sina.MAX_BYTES + 1), b'not JSON', b'{"status":1,"status":2}', json.dumps(invalid).encode()]:
            def runner(_command, **kwargs):
                kwargs['stdout'].write(raw)
                return types.SimpleNamespace(returncode=0)
            with self.subTest(size=len(raw)), self.assertRaises(sina.SinaResponseError):
                probe.probe(runner=runner, now=NOW)

    def test_parent_rechecks_encoding_boundary_prices_dates_and_counts(self):
        for mutate in [lambda s: s['encoding'].update(wrapper='KLC_K2_sh515450'),
                       lambda s: s['encoding'].update(version=True),
                       lambda s: s['last_bar'].update(close='99'),
                       lambda s: s['last_bar'].update(time='2025-01-03'),
                       lambda s: s.update(selected_count=1),
                       lambda s: s.update(raw_count=1)]:
            summary = worker_summary(); mutate(summary)
            def runner(_command, **kwargs):
                kwargs['stdout'].write(json.dumps(summary).encode())
                return types.SimpleNamespace(returncode=0)
            with self.subTest(mutate=mutate), self.assertRaises(sina.SinaError):
                probe.probe(runner=runner, now=NOW)

    def test_utf8_chinese_summary_works_with_cp1252_redirected_stdout(self):
        buffer = io.BytesIO()
        output = io.TextIOWrapper(buffer, encoding='cp1252')
        with patch.object(sys, 'stdout', output), patch.object(probe, 'probe', return_value=worker_summary()):
            code = probe._main([])
        output.flush()
        text = buffer.getvalue().decode('utf-8')
        self.assertEqual(code, 0)
        self.assertIn('核验：通过', text)
        self.assertIn('非完整交易所日历', text)
        self.assertIn('未改变数据源门禁', text)
        output.detach()

    def test_cli_failure_is_unavailable_fixed_type_without_sensitive_details(self):
        output = io.StringIO()
        with patch.object(sys, 'stdout', output), patch.object(probe, 'probe', side_effect=RuntimeError('secret response path key')):
            code = probe._main([])
        self.assertEqual(code, 1)
        self.assertIn('unavailable', output.getvalue())
        self.assertNotIn('secret', output.getvalue())
        self.assertNotIn('path', output.getvalue())

    def test_unknown_arguments_do_not_echo_values(self):
        output = io.StringIO()
        with patch.object(sys, 'stdout', output):
            code = probe._main(['--unknown-private-value'])
        self.assertEqual(code, 1)
        self.assertNotIn('unknown-private', output.getvalue())


if __name__ == '__main__':
    unittest.main()
