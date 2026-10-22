"""Fictional, offline tests for the optional Sina candidate; no live claims."""
import copy
from datetime import date
import hashlib
from importlib import metadata
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import sina


BODY = b'var KLC_KL_sh515450="HX/FAKE+ABC/123";\n'


class Response:
    def __init__(self, body=BODY, url=sina.URL, status=200, length=None):
        self.body, self.url, self.status = body, url, status
        self.headers = {} if length is None else {'Content-Length': str(length)}
        self.read_limit = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def geturl(self):
        return self.url

    def read(self, limit):
        self.read_limit = limit
        return self.body[:limit]


def bars():
    return [dict(date=d, open=1.1, high=1.2, low=1.0, close=1.15, volume=100)
            for d in ('2025-01-02', '2025-01-03', '2025-01-06')]


def fake_worker(decoded=None, response=None, begin='2025-01-03', end='2025-01-06'):
    return sina._fetch_worker('515450.SH', begin, end,
                              opener=lambda *_args, **_kwargs: response or Response(),
                              decoder=lambda payload: bars() if decoded is None else decoded)


class SinaCandidateTests(unittest.TestCase):
    def test_single_complete_trailing_comment_preserves_body_hash_and_payload(self):
        for wrapper, payload in [('KLC_KL_sh515450', 'HX/FICTIONAL'), ('KLC_K2_sh515450', 'K2/3rPXFICTIONAL')]:
            base = ('var ' + wrapper + '="' + payload + '";').encode()
            for tail in (b'\r\n/**/\r\n', b'\n/* cache: source=public; https://example.invalid/a */\n',
                         '\n/* 新浪公开日线：测试注释。 */\n'.encode()):
                body = base + tail
                with self.subTest(wrapper=wrapper, tail=tail):
                    downloaded = sina._download(lambda *_a, **_k: Response(body=body, length=len(body)))
                    self.assertEqual(downloaded.text.encode(), body)
                    self.assertEqual(downloaded.compressed, payload)
                    self.assertEqual(downloaded.response_sha256, hashlib.sha256(body).hexdigest())
                    self.assertEqual(downloaded.encoding['response_bytes'], len(body))

    def test_truncated_nested_executable_or_abnormal_trailers_fail_before_decode(self):
        base = b'var KLC_K2_sh515450="K2/3rPXFICTIONAL";'
        tails = [b'/*', b'/* no closing delimiter', b'/* x * /', b'*/', b'/* outer /* inner */',
                 b'/* a */ /* b */', b'/* ok */alert(1)', b'/* ok */;var x=1;',
                 b'/* "*/alert(1)// */', b'// not a block comment', b'/*ok*/\x00', b'/*\x00*/', b'/*\x1b*/',
                 '/*\u202e*/'.encode(), '/*\u200b*/'.encode(), '/*\u2028*/'.encode(),
                 b'/*ok*/' + '\u00a0'.encode(), b'/*' + b'x' * 4096 + b'*/', b' ' * 4097,
                 b'/* ok */var KLC_K2_sh515450="K2/AAAA";']
        for tail in tails:
            with self.subTest(tail=tail[:80]), self.assertRaises(sina.SinaResponseError):
                sina._fetch_worker('515450.SH', '2025-01-02', '2025-01-06',
                    opener=lambda *_a, **_k: Response(body=base + tail),
                    decoder=lambda _s: self.fail('invalid trailer must fail before decoder'))

    def test_trailer_length_boundary_and_placement_are_checked(self):
        base = b'var KLC_K2_sh515450="K2/3rPXFICTIONAL";'
        tail = b'/*' + b'x' * (4096 - 4) + b'*/'
        downloaded = sina._download(lambda *_a, **_k: Response(body=base + tail))
        self.assertTrue(downloaded.text.endswith(tail.decode()))
        for body in [b'/* comment */' + base, base[:-1] + b'/* comment */',
                     base.replace(b'sh515450', b'sh600900') + b'/* comment */',
                     base.replace(b'K2/', b'K2+') + b'/* comment */']:
            with self.subTest(body=body), self.assertRaises(sina.SinaError):
                sina._download(lambda *_a, **_k: Response(body=body))

    def test_both_known_wrappers_bind_the_exact_symbol_and_encoding_header(self):
        for wrapper, payload, codec in [('KLC_KL_sh515450', 'HX/FICTIONAL', 1479),
                                        ('KLC_K2_sh515450', 'K2/3rPXFICTIONAL', 3466)]:
            body = ('var ' + wrapper + '="' + payload + '";').encode()
            downloaded = sina._download(lambda *_a, **_k: Response(body=body))
            self.assertEqual(downloaded.text.encode(), body)
            self.assertEqual(downloaded.compressed, payload)
            self.assertEqual(downloaded.encoding, dict(wrapper=wrapper, codec=codec, version=0, response_bytes=len(body)))

    def test_unknown_codec_version_or_wrapper_payload_mismatch_fails_before_decode(self):
        for wrapper, payload in [('KL', 'K2/3rPX'), ('K2', 'HX/FICTIONAL'), ('K2', 'K2+3rPX'),
                                 ('K2', 'AAA3rPX'), ('K2', 'K2/'), ('KL', 'HX/FAKE=')]:
            body = ('var KLC_' + wrapper + '_sh515450="' + payload + '";').encode()
            with self.subTest(wrapper=wrapper, payload=payload), self.assertRaises(sina.SinaError):
                sina._fetch_worker('515450.SH', '2025-01-02', '2025-01-06',
                    opener=lambda *_a, **_k: Response(body=body), decoder=lambda _s: self.fail('must reject before decoder'))

    def test_k2_wrong_symbol_or_multiple_assignments_are_rejected(self):
        for body in [b'var KLC_K2_sh600900="K2/AAAA";', b'var KLC_K2_sz515450="K2/AAAA";',
                     b'var KLC_K2_sh515450="K2/AAAA";var x=1;', b'var KLC_K3_sh515450="K2/AAAA";']:
            with self.subTest(body=body), self.assertRaises(sina.SinaResponseError):
                sina._download(lambda *_a, **_k: Response(body=body))

    def test_whole_history_validated_then_explicitly_clipped_without_sorting(self):
        result = fake_worker()
        self.assertEqual([r['time'] for r in result['rows']], ['2025-01-03', '2025-01-06'])
        self.assertEqual((result['raw_start'], result['raw_end'], result['raw_count']), ('2025-01-02', '2025-01-06', 3))
        self.assertEqual(result['response_sha256'], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(result['adjustment'], 'unknown')
        self.assertIn('未核验', result['volume_unit'])
        self.assertEqual(result['rows'][0]['close'], '1.15')

    def test_download_uses_exact_https_url_timeout_and_bounded_read(self):
        response, calls = Response(), []
        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            return response
        sina._fetch_worker('515450.SH', date(2025, 1, 2), date(2025, 1, 6), opener=opener, decoder=lambda _s: bars())
        self.assertEqual(calls, [(sina.URL, 15)])
        self.assertEqual(response.read_limit, 8_000_001)

    def test_wrong_symbol_scope_or_date_rejected_before_network(self):
        for symbol, begin, end in [('600900.SH', '2025-01-02', '2025-01-06'),
                                   ('sh515450', '2025-01-02', '2025-01-06'),
                                   ('515450.SH', '2025-02-30', '2025-03-01'),
                                   ('515450.SH', '2025-01-06', '2025-01-02')]:
            with self.subTest(symbol=symbol, begin=begin), self.assertRaises(sina.SinaResponseError):
                sina._fetch_worker(symbol, begin, end, opener=lambda *_a, **_k: self.fail('network called'))

    def test_wrong_wrapper_code_truncation_or_appended_script_is_rejected(self):
        for body in [b'var KLC_KL_sh600900="FAKE";', b'var KLC_KL_sh515450="FAKE',
                     b'var KLC_KL_sh515450="FAKE";alert(1)', b'<html>403</html>',
                     b'var KLC_KL_sh515450="";', b'var KLC_KL_sh515450="a\\"b";', b'\xff']:
            with self.subTest(body=body), self.assertRaises(sina.SinaResponseError):
                fake_worker(response=Response(body=body))

    def test_response_status_redirect_size_and_content_length_are_checked(self):
        for response in [Response(status=404), Response(url='https://example.invalid/redirect'),
                         Response(body=b'x' * (sina.MAX_BYTES + 1)), Response(length=len(BODY) + 10),
                         Response(length=sina.MAX_BYTES + 1), Response(length='invalid')]:
            with self.subTest(status=response.status, length=response.headers), self.assertRaises(sina.SinaResponseError):
                fake_worker(response=response)

    def test_real_redirect_handler_refuses_without_following(self):
        with self.assertRaises(sina.SinaResponseError):
            sina._NoRedirect().redirect_request(Request(sina.URL), None, 302, 'found', {}, 'https://example.invalid/')

    def test_http_error_and_download_timeout_are_fixed_errors(self):
        for error, expected in [(HTTPError(sina.URL, 403, 'secret body', {}, None), sina.SinaResponseError),
                                (TimeoutError('sensitive transport text'), sina.SinaTimeoutError)]:
            def opener(*_a, **_k):
                raise error
            with self.assertRaises(expected) as result:
                sina._fetch_worker('515450.SH', '2025-01-02', '2025-01-06', opener=opener)
            self.assertEqual(str(result.exception), '新浪候选数据不可用')

    def test_invalid_raw_bars_including_outside_requested_window_are_not_hidden(self):
        mutations = [lambda data: data.reverse(), lambda data: data.append(copy.deepcopy(data[-1])),
                     lambda data: data[0].update(date='2099-01-01'), lambda data: data[0].update(close=5),
                     lambda data: data[0].update(open=0), lambda data: data[0].update(volume=-1),
                     lambda data: data[0].update(volume=float('nan')), lambda data: data[0].update(high=float('inf')),
                     lambda data: data[0].update(prevclose=float('nan')), lambda data: data[0].update(volume=None),
                     lambda data: data[0].update(volume=True), lambda data: data[0].update(secret='ignored?'),
                     lambda data: data[0].update(date='2025-01-02T01:00:00.000Z')]
        for mutate in mutations:
            data = bars(); mutate(data)
            with self.subTest(mutation=mutate), self.assertRaises(sina.SinaDecodeError):
                fake_worker(decoded=data)
        for data in [[], {}, None]:
            if data is None:
                with self.assertRaises(sina.SinaDecodeError):
                    sina._normalise_rows(data)
            else:
                with self.assertRaises(sina.SinaDecodeError):
                    fake_worker(decoded=data)

    def test_decoder_utc_midnight_dates_and_known_optional_fields(self):
        data = bars()
        data[0].update(date='2025-01-02T00:00:00.000Z', prevclose=1.02)
        data[1]['amount'] = 200
        result = fake_worker(decoded=data, begin='2025-01-02')
        self.assertEqual(result['rows'][0]['time'], '2025-01-02')
        self.assertNotIn('prevclose', result['rows'][0])

    def test_no_data_in_requested_range_is_unavailable(self):
        with self.assertRaises(sina.SinaDecodeError):
            fake_worker(begin='2025-02-01', end='2025-02-03')

    def test_only_local_decoder_is_evaluated_with_limits(self):
        calls = []
        class Engine:
            def eval(self, code, **kwargs):
                calls.append(('eval', code, kwargs))
            def call(self, name, payload, **kwargs):
                calls.append(('call', name, payload, kwargs))
                return bars()
        result = sina._decode_payload('COMPRESSED', runtime_loader=lambda: ('LOCAL STATIC JS', Engine))
        self.assertEqual(result, bars())
        limits = {'timeout': 5000, 'max_memory': 128 * 1024 * 1024}
        self.assertEqual(calls, [('eval', 'LOCAL STATIC JS', limits), ('call', 'd', 'COMPRESSED', limits)])

    def test_decoder_errors_are_sanitized(self):
        class Engine:
            def eval(self, *_a, **_k):
                raise RuntimeError('sensitive payload text')
        with self.assertRaises(sina.SinaDecodeError) as result:
            sina._decode_payload('FAKE', runtime_loader=lambda: ('STATIC', Engine))
        self.assertNotIn('sensitive', str(result.exception))

    def test_javascript_timeout_has_a_fixed_timeout_type(self):
        class JSTimeoutException(Exception):
            pass
        class Engine:
            def eval(self, *_a, **_k):
                raise JSTimeoutException('untrusted detail')
        with self.assertRaises(sina.SinaTimeoutError) as result:
            sina._decode_payload('FAKE', runtime_loader=lambda: ('STATIC', Engine))
        self.assertEqual(str(result.exception), '新浪候选数据不可用')

    def test_optional_dependency_missing_or_wrong_version_fails_without_network(self):
        with patch.object(sina.metadata, 'version', side_effect=metadata.PackageNotFoundError):
            with self.assertRaises(sina.SinaDependencyError):
                sina._optional_runtime()
        with patch.object(sina.metadata, 'version', return_value='999'):
            with self.assertRaises(sina.SinaDependencyError):
                sina._optional_runtime()
        for platform, required, version, conflicting in [('linux', 'py-mini-racer', '0.6.0', 'mini-racer'),
                                                         ('win32', 'mini-racer', '0.14.1', 'py-mini-racer')]:
            versions = {'akshare': '1.19.1', required: version}
            def lookup(name):
                if name not in versions:
                    raise metadata.PackageNotFoundError(name)
                return versions[name]
            with self.subTest(platform=platform), patch.object(sina.sys, 'platform', platform), patch.object(sina.metadata, 'version', side_effect=lookup):
                sina._check_runtime_dependencies()
                versions[conflicting] = '0.0.0'
                with self.assertRaises(sina.SinaDependencyError):
                    sina._check_runtime_dependencies()

    def test_outer_worker_is_time_bounded_has_no_stderr_and_checks_output(self):
        expected = fake_worker()
        def runner(command, **kwargs):
            self.assertEqual(command[1:], ['-m', 'goose_research.sina', '--worker', 'sh515450', '2025-01-03', '2025-01-06'])
            self.assertEqual(kwargs['timeout'], 45)
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertFalse(kwargs['check'])
            self.assertEqual(Path(kwargs['cwd']), Path(sina.__file__).resolve().parents[1])
            kwargs['stdout'].write(json.dumps(expected).encode())
            return types.SimpleNamespace(returncode=0)
        self.assertEqual(sina.fetch_sina_rows('515450.SH', '2025-01-03', '2025-01-06', runner=runner), expected)

    def test_outer_timeout_and_dependency_failure_have_fixed_types(self):
        def timeout(*_a, **_k):
            raise subprocess.TimeoutExpired('never expose command', 45)
        with self.assertRaises(sina.SinaTimeoutError):
            sina.fetch_sina_rows('515450.SH', '2025-01-03', '2025-01-06', runner=timeout)
        for category, error in [('dependency', sina.SinaDependencyError), ('encoding', sina.SinaEncodingError)]:
            def missing(_command, **kwargs):
                kwargs['stdout'].write(json.dumps({'error': category}).encode())
                return types.SimpleNamespace(returncode=1)
            with self.subTest(category=category), self.assertRaises(error):
                sina.fetch_sina_rows('515450.SH', '2025-01-03', '2025-01-06', runner=missing)

    def test_outer_rejects_large_invalid_duplicate_and_nonfinite_protocol(self):
        for body in [b'x' * (sina.MAX_BYTES + 1), b'not json', b'{"rows":[],"rows":[]}', b'{"rows":NaN}']:
            def runner(_command, **kwargs):
                kwargs['stdout'].write(body)
                return types.SimpleNamespace(returncode=0)
            with self.subTest(size=len(body)), self.assertRaises(sina.SinaResponseError):
                sina.fetch_sina_rows('515450.SH', '2025-01-03', '2025-01-06', runner=runner)

    def test_outer_rechecks_worker_identity_metadata_and_rows(self):
        for mutate in [lambda result: result.update(requested_url='http://wrong.invalid'),
                       lambda result: result.update(decoder='unversioned'),
                       lambda result: result['encoding'].update(codec=1478),
                       lambda result: result['rows'].reverse(),
                       lambda result: result['rows'][0].update(close='99'),
                       lambda result: result.update(response_sha256='bad'),
                       lambda result: result.update(raw_count=True),
                       lambda result: result.update(raw_count=1),
                       lambda result: result.update(raw_count=99999)]:
            result = fake_worker(); mutate(result)
            def runner(_command, **kwargs):
                kwargs['stdout'].write(json.dumps(result).encode())
                return types.SimpleNamespace(returncode=0)
            with self.subTest(mutation=mutate), self.assertRaises(sina.SinaResponseError):
                sina.fetch_sina_rows('515450.SH', '2025-01-03', '2025-01-06', runner=runner)


if __name__ == '__main__':
    unittest.main()
