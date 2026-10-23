"""Offline diagnostic regressions: only explicit fictional responses/metadata."""
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import diagnose_sina as diag, market, probe_sina as probe, sina

SECRET = 'PRIVATE_CANARY_C:/Users/hidden/BARK_KEY_NEVER_PRINT'
BODY = b'var KLC_K2_sh515450="K2/FICTIONAL";\n/* ' + SECRET.encode() + b' */'
NOW = datetime(2025, 1, 6, 9, tzinfo=timezone.utc)


class Response:
    def __init__(self, body=BODY, length=None, status=200, url=sina.URL):
        self.body, self.status, self.url = body, status, url
        self.headers = {'Content-Length': str(len(body)) if length is None else length}
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def geturl(self): return self.url
    def read(self, _limit): return self.body


def bars():
    return [dict(date=d, open=1.1, high=1.2, low=1.0, close=1.15, volume=100)
            for d in ('2025-01-02', '2025-01-03', '2025-01-06')]


def wire(response=None, *, opener=None, decoder=None, verifier=None, expected='2025-01-06', change=None):
    messages = [dict(kind='identity', identity=diag.identity())]
    trace = diag.Trace(lambda event: messages.append(copy.deepcopy(event)))
    try:
        summary = probe._probe_worker('2022-01-06', expected, probe.BASES[0],
            opener=opener or (lambda *_a, **_k: response or Response()),
            decoder=decoder or (lambda _s: bars()), verifier=verifier or (lambda *_a: True), now=NOW, diagnostic=trace)
        trace.begin('summary', 'validate')
        probe._validate_summary(summary, '2022-01-06', expected, probe.BASES[0])
        trace.passed('summary')
        result = dict(kind='result', status='observed', error_type=None)
    except Exception as exc:
        trace.failed()
        result = dict(kind='result', status='unavailable', error_type=diag._error_type(exc))
    messages.append(result)
    if change:
        change(messages)
    return b''.join(json.dumps(m, ensure_ascii=True).encode() + b'\n' for m in messages), result['status']


def execute(raw, status='unavailable', *, timeout=False):
    def runner(_command, **kwargs):
        kwargs['stdout'].write(raw)
        if timeout:
            raise subprocess.TimeoutExpired(SECRET, 45)
        return SimpleNamespace(returncode=0 if status == 'observed' else 1)
    return diag.diagnose(runner=runner, now=NOW)


class DiagnosticTests(unittest.TestCase):
    def test_17915_header_and_body_pass_length_without_relaxing_validation(self):
        prefix, suffix = b'var KLC_K2_sh515450="K2/', b'";\n/* public note */'
        body = prefix + b'A' * (17915 - len(prefix) - len(suffix)) + suffix
        response = Response(body, length='17915')
        trace = diag.Trace()
        trace.begin('input', 'dates'); trace.passed('input')
        result = sina._download(lambda *_a, **_k: response, diagnostic=trace)
        length = next(s for s in trace.items if s['stage'] == 'length')
        self.assertEqual(length['state'], 'passed')
        self.assertEqual(length['check'], 'exact_match')
        self.assertEqual(length['metrics']['declared_bytes'], 17915)
        self.assertEqual(length['metrics']['received_bytes'], 17915)
        self.assertTrue(length['metrics']['length_matches'])
        self.assertEqual(length['metrics']['header_char_count'], 5)
        self.assertEqual(length['metrics']['header_non_digit_count'], 0)
        self.assertEqual(result.encoding['response_bytes'], 17915)

    def test_each_validation_failure_has_exact_stage_and_check(self):
        cases = [
            (Response(status=403), 'http', 'identity'),
            (Response(url='https://example.invalid/' + SECRET), 'http', 'identity'),
            (Response(length='17915 ' + SECRET), 'length', 'header_format'),
            (Response(length=17915), 'length', 'header_format'),
            (Response(length='9' * 5000), 'length', 'header_parse'),
            (Response(length='8000001'), 'length', 'declared_limit'),
            (Response(length='17915'), 'length', 'exact_match'),
            (Response(body=b'', length='0'), 'length', 'body_limit'),
            (Response(body=b'\xff'), 'wrapper', 'utf8'),
            (Response(body=b'var wrong="' + SECRET.encode() + b'";'), 'wrapper', 'assignment'),
            (Response(body=BODY + b'alert(1)'), 'trailer', 'boundary'),
            (Response(body=BODY.replace(SECRET.encode(), b'\x00')), 'trailer', 'content'),
            (Response(body=BODY.replace(b'K2/', b'K2+')), 'encoding', 'wrapper_match'),
        ]
        for response, stage, check in cases:
            raw, status = wire(response)
            report = execute(raw, status)
            with self.subTest(stage=stage, check=check):
                self.assertEqual((report['failed_stage'], report['failed_check']), (stage, check))
                self.assertEqual(report['status'], 'unavailable')
                self.assertNotIn(SECRET, json.dumps(report))

    def test_body_read_error_and_open_error_are_distinct_and_sanitized(self):
        def unavailable(*_a, **_k): raise URLError(SECRET)
        raw, status = wire(opener=unavailable)
        report = execute(raw, status)
        self.assertEqual((report['failed_stage'], report['failed_check']), ('http', 'open'))
        class Broken(Response):
            def read(self, _limit): raise ConnectionResetError(SECRET)
        report = execute(*wire(Broken()))
        self.assertEqual((report['failed_stage'], report['failed_check']), ('length', 'read'))
        self.assertNotIn(SECRET, json.dumps(report))

    def test_decode_api_and_freshness_are_distinguishable(self):
        for options, stage, check in [
            ({'decoder': lambda _s: [dict(bars()[0], close=float('nan'))]}, 'decode', 'rows'),
            ({'verifier': lambda *_a: False}, 'api', 'compare'),
            ({'expected': '2025-01-07'}, 'freshness', 'last_date'),
        ]:
            report = execute(*wire(**options))
            with self.subTest(stage=stage):
                self.assertEqual((report['failed_stage'], report['failed_check']), (stage, check))

    def test_success_has_no_prices_response_headers_paths_or_holdings(self):
        report = execute(*wire())
        self.assertEqual(report['status'], 'observed')
        self.assertTrue(report['worker_matches_parent'])
        self.assertEqual(report['not_reached'], [])
        self.assertFalse(report['gate_changed'])
        self.assertTrue(report['source_gate_enabled'])
        self.assertEqual(report['source_usage'], 'raw_observation_only')
        for key in ('price_unit_verified', 'volume_unit_verified', 'adjustment_verified',
                    'valuation_enabled', 'volume_analysis_enabled', 'advice_enabled'):
            self.assertIs(report[key], False)
        text = json.dumps(report)
        for forbidden in (SECRET, 'K2/FICTIONAL', 'first_bar', 'last_bar', 'open', 'high', 'volume', 'rows', 'Content-Length'):
            # Stage/check names such as "rows" are allowed; price-bearing keys aren't.
            if forbidden in ('open', 'rows', 'volume'):
                self.assertNotIn('"' + forbidden + '":', text)
            else:
                self.assertNotIn(forbidden, text)

    def test_missing_length_is_unknown_not_zero(self):
        response = Response()
        response.headers = {}
        report = execute(*wire(response))
        self.assertEqual(report['status'], 'observed')
        length = next(s for s in report['stages'] if s['stage'] == 'length')
        self.assertIsNone(length['metrics']['declared_bytes'])
        self.assertIsNone(length['metrics']['length_matches'])

    def test_timeout_retains_last_flushed_phase_without_raw_exception(self):
        raw, _ = wire()
        messages = raw.splitlines()
        chosen = []
        for line in messages:
            chosen.append(line + b'\n')
            item = json.loads(line)
            if item.get('stage') == 'decode' and item.get('state') == 'running':
                break
        report = execute(b''.join(chosen) + b'{interrupted', timeout=True)
        self.assertEqual(report['error_type'], 'SinaTimeoutError')
        self.assertEqual(report['failed_stage'], 'decode')
        self.assertNotIn(SECRET, json.dumps(report))

    def test_bad_protocol_unknown_metric_and_false_success_fail_closed(self):
        def private_metric(messages):
            messages[1]['metrics']['response_body'] = SECRET
        def false_success(messages):
            del messages[1:-1]
        valid, status = wire()
        for raw in (b'not-json ' + SECRET.encode(), b'x' * (diag.MAX_PROTOCOL + 1),
                    wire(change=private_metric)[0], wire(change=false_success)[0], valid + SECRET.encode()):
            report = execute(raw, status)
            self.assertEqual(report['status'], 'unavailable')
            self.assertEqual(report['failed_stage'], 'worker')
            self.assertNotIn(SECRET, json.dumps(report))

    def test_parent_worker_code_mismatch_is_unavailable(self):
        def mismatch(messages): messages[0]['identity']['download_code_sha256'] = '0' * 64
        report = execute(*wire(change=mismatch))
        self.assertFalse(report['worker_matches_parent'])
        self.assertEqual((report['failed_stage'], report['failed_check']), ('worker', 'code_identity'))

    def test_code_fingerprint_excludes_local_filenames_and_captures_logic(self):
        one = compile('x = 1', SECRET, 'exec')
        two = compile('x = 1', 'another path', 'exec')
        other = compile('x = 2', SECRET, 'exec')
        self.assertEqual(diag._code_hash(one), diag._code_hash(two))
        self.assertNotEqual(diag._code_hash(one), diag._code_hash(other))
        self.assertNotIn(str(Path(sina.__file__).parent), json.dumps(diag.identity()))

    def test_real_zip_no_git_subprocess_uses_same_code_and_outputs_only_metadata(self):
        # Actual Windows/POSIX subprocess. Only HTTP transport is replaced;
        # exactly 17915 received bytes reach wrapper validation, no network.
        program = '''
import runpy, sys
from types import SimpleNamespace
from goose_research import sina
class Response:
    status = 200
    headers = {'Content-Length': '17915'}
    def __enter__(self): return self
    def __exit__(self, *_a): pass
    def geturl(self): return sina.URL
    def read(self, limit): return b'x' * 17915
sina.build_opener = lambda *_a: SimpleNamespace(open=lambda *_a, **_k: Response())
sys.argv = ['diagnose_sina', '--worker'] + sys.argv[1:]
runpy.run_module('goose_research.diagnose_sina', run_name='__main__', alter_sys=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            zip_p1 = Path(directory) / 'sourceZIP' / 'p1'
            shutil.copytree(Path(sina.__file__).parent, zip_p1 / 'goose_research',
                            ignore=shutil.ignore_patterns('__pycache__'))
            def runner(command, **kwargs):
                kwargs['env'] = dict(os.environ, PATH='', PYTHONIOENCODING='cp1252')
                kwargs['cwd'] = zip_p1
                return subprocess.run([sys.executable, '-c', program] + command[-3:], **kwargs)
            report = diag.diagnose(runner=runner, now=NOW)
        self.assertTrue(report['worker_matches_parent'])
        self.assertEqual((report['failed_stage'], report['failed_check']), ('wrapper', 'assignment'))
        length = next(s for s in report['stages'] if s['stage'] == 'length')
        self.assertEqual(length['metrics']['received_bytes'], 17915)
        self.assertEqual(length['state'], 'passed')

    def test_cli_unknown_argument_never_prints_argument_or_traceback_cp1252(self):
        env = dict(os.environ, PATH='', PYTHONIOENCODING='cp1252')
        result = subprocess.run([sys.executable, '-m', 'goose_research.diagnose_sina', '--' + SECRET],
            cwd=Path(sina.__file__).resolve().parents[1], env=env, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b'')
        self.assertNotIn(SECRET, result.stdout.decode('utf-8'))
        report = json.loads(result.stdout)
        self.assertEqual(report['failed_stage'], 'input')

    def test_cli_failure_result_only_json_no_normal_quote_summary(self):
        report = execute(*wire(Response(length='bad ' + SECRET)))
        output = io.StringIO()
        with patch.object(sys, 'stdout', output), patch.object(diag, 'diagnose', return_value=report):
            self.assertEqual(diag._main([]), 1)
        self.assertEqual(json.loads(output.getvalue()), report)
        self.assertNotIn(SECRET, output.getvalue())


if __name__ == '__main__':
    unittest.main()
