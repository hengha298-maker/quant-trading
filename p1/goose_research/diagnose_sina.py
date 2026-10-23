"""Opt-in, read-only, allowlisted diagnostics. Never emits response text or paths."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import marshal
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import CodeType

from . import market, probe_sina as probe, sina

REVISION = 'sina-stages-v1'
MAX_PROTOCOL = 128_000
CHECKS = {
    'input': ('dates',), 'http': ('open', 'identity'),
    'length': ('header_read', 'header_format', 'header_parse', 'declared_limit', 'read', 'body_limit', 'exact_match'),
    'wrapper': ('utf8', 'assignment'), 'trailer': ('limit', 'absent', 'boundary', 'content'),
    'encoding': ('header', 'wrapper_match'),
    'decode': ('dependencies', 'engine', 'static_decoder', 'payload', 'rows'),
    'api': ('compare',), 'freshness': ('last_date',), 'summary': ('validate',),
    'worker': ('launch', 'protocol', 'code_identity', 'timeout'),
}
STAGES = tuple(CHECKS)[:-1]
ERRORS = ('SinaError', 'SinaResponseError', 'SinaDependencyError', 'SinaTimeoutError',
          'SinaEncodingError', 'SinaDecodeError', 'SinaApiMismatchError', 'SinaStaleDataError')
BOOLS = {'url_matches', 'header_present', 'header_is_text', 'header_ascii_digits',
         'declared_exceeds_limit', 'body_is_bytes', 'length_matches', 'comment_present',
         'boundary_complete', 'nested_comment', 'content_printable'}
COUNTS = {'declared_bytes', 'received_bytes', 'header_char_count', 'header_non_digit_count',
          'trailer_chars', 'codec', 'codec_version', 'raw_count', 'selected_count'}
DATES = {'expected_date', 'latest_date'}
KNOWN_SOURCES = {
    '0e0d35b4d7875f90ec40c1abacdb108cbd48c6ae90e68f969ddaf0769aa1881c': 'bdc5afe',
    '3b33b71b82df75b83098252aea358f9b72ea89ef3d962f9352d7ff74961e80ca': 'ddd0297',
}


def require(condition):
    if not condition:
        raise sina.SinaResponseError()


def _metrics(values):
    require(isinstance(values, dict) and values.keys() <= BOOLS | COUNTS | DATES | {'http_status'})
    for key, value in values.items():
        if value is None:
            continue
        if key in BOOLS:
            require(type(value) is bool)
        elif key in COUNTS:
            require(type(value) is int and 0 <= value <= 8_000_001)
        elif key == 'http_status':
            require(type(value) is int and 100 <= value <= 599)
        else:
            require(isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value))


class Trace:
    def __init__(self, emit=None):
        self.items, self.emit = [], emit

    def _event(self, item):
        if self.emit:
            self.emit(dict(kind='stage', **item))

    def begin(self, stage, check, **metrics):
        require(stage in CHECKS and check in CHECKS[stage])
        _metrics(metrics)
        if self.items and self.items[-1]['stage'] == stage:
            require(self.items[-1]['state'] == 'running')
            item = self.items[-1]
            item['metrics'].update(metrics)
            item['check'] = check
        else:
            require(not any(i['stage'] == stage for i in self.items))
            if stage != 'worker':
                prior = [i['stage'] for i in self.items]
                require(prior == list(STAGES[:len(prior)]) and stage == STAGES[len(prior)])
                require(all(i['state'] == 'passed' for i in self.items))
            item = dict(stage=stage, check=check, state='running', metrics=dict(metrics))
            self.items.append(item)
        self._event(item)

    def passed(self, stage):
        require(self.items and self.items[-1]['stage'] == stage and self.items[-1]['state'] == 'running')
        self.items[-1]['state'] = 'passed'
        self._event(self.items[-1])

    def failed(self):
        if not self.items or self.items[-1]['state'] != 'running':
            self.begin('worker', 'protocol')
        self.items[-1]['state'] = 'failed'
        self._event(self.items[-1])


def _source_hash(module):
    try:
        # Only our source files are read. No Git, env dump or directory scan.
        with open(module.__file__, encoding='utf-8') as stream:
            source = stream.read(1_000_001)
        return hashlib.sha256(source.encode('utf-8')).hexdigest() if len(source) <= 1_000_000 else None
    except (OSError, UnicodeError, TypeError, AttributeError):
        return None


def _code_hash(code):
    def canonical(value):
        if isinstance(value, CodeType):
            return value.replace(co_filename='<P1>', co_firstlineno=1,
                co_consts=tuple(canonical(c) for c in value.co_consts))
        return value
    return hashlib.sha256(marshal.dumps(canonical(code))).hexdigest()


def identity():
    source = _source_hash(sina)
    try:
        location_matches = Path(sina._download.__code__.co_filename).resolve() == Path(sina.__file__).resolve()
    except (OSError, RuntimeError, ValueError):
        location_matches = False
    return dict(revision=REVISION, sina_source_sha256=source, probe_source_sha256=_source_hash(probe),
                sina_implementation='sina-stages-v1' if getattr(sina, 'IMPLEMENTATION', None) == 'sina-stages-v1' else 'legacy_or_unknown',
                diagnostic_source_sha256=_source_hash(sys.modules[__name__]),
                download_code_sha256=_code_hash(sina._download.__code__),
                probe_code_sha256=_code_hash(probe._probe_worker.__code__),
                download_first_line=sina._download.__code__.co_firstlineno,
                source_matches_code_file=location_matches, max_bytes=sina.MAX_BYTES,
                known_sina_source=KNOWN_SOURCES.get(source, 'unlisted'),
                python_version='.'.join(str(n) for n in sys.version_info[:3]),
                platform='windows' if sys.platform == 'win32' else 'linux' if sys.platform == 'linux' else 'other')


def _validate_identity(value):
    keys = {'revision', 'sina_implementation', 'sina_source_sha256', 'probe_source_sha256', 'diagnostic_source_sha256',
            'download_code_sha256', 'probe_code_sha256', 'download_first_line', 'source_matches_code_file',
            'max_bytes', 'known_sina_source', 'python_version', 'platform'}
    require(isinstance(value, dict) and value.keys() == keys and value['revision'] == REVISION)
    for key in keys:
        if key.endswith('_sha256'):
            require(value[key] is None or isinstance(value[key], str) and re.fullmatch('[0-9a-f]{64}', value[key]))
    require(type(value['download_first_line']) is int and 1 <= value['download_first_line'] <= 1_000_000)
    require(type(value['max_bytes']) is int and 0 < value['max_bytes'] <= 2**31 - 1)
    require(type(value['source_matches_code_file']) is bool)
    require(value['known_sina_source'] in ('bdc5afe', 'ddd0297', 'unlisted'))
    require(isinstance(value['python_version'], str) and re.fullmatch(r'[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}', value['python_version']))
    require(value['platform'] in ('windows', 'linux', 'other'))
    require(value['sina_implementation'] in ('sina-stages-v1', 'legacy_or_unknown'))
    return value


def _error_type(exc):
    name = type(exc).__name__
    return name if isinstance(exc, sina.SinaError) and name in ERRORS else 'SinaResponseError'


def _report(code, trace, status, error, worker_matches=None):
    failed = next((i for i in reversed(trace.items) if i['state'] == 'failed'), None)
    return dict(diagnostic_revision=REVISION, status=status, error_type=error,
                failed_stage=None if failed is None else failed['stage'],
                failed_check=None if failed is None else failed['check'],
                code=code, worker_matches_parent=worker_matches, stages=trace.items,
                not_reached=[s for s in STAGES if not any(i['stage'] == s for i in trace.items)],
                gate_changed=False, source_gate_enabled=market.SINA_DAILY_OBSERVATION_ENABLED is True,
                source_usage='raw_observation_only', price_unit_verified=False,
                volume_unit_verified=False, adjustment_verified=False,
                valuation_enabled=False, volume_analysis_enabled=False, advice_enabled=False)


def _worker(argv):
    output = sys.stdout.buffer
    def emit(value):
        output.write(json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii') + b'\n')
        output.flush()
    trace = Trace(emit)
    # Capture only fixed-schema identities and phase events before redirecting
    # incidental library output. Progress survives a killed/timeout child.
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        try:
            emit(dict(kind='identity', identity=identity()))
            require(len(argv) == 3)
            summary = probe._probe_worker(argv[0], argv[1], argv[2], diagnostic=trace)
            trace.begin('summary', 'validate')
            probe._validate_summary(summary, argv[0], argv[1], argv[2])
            trace.passed('summary')
            emit(dict(kind='result', status='observed', error_type=None))
            return 0
        except Exception as exc:
            trace.failed()
            emit(dict(kind='result', status='unavailable', error_type=_error_type(exc)))
            return 1


def _consume(raw, trace, *, timed_out=False):
    require(isinstance(raw, bytes) and len(raw) <= MAX_PROTOCOL)
    lines = raw.splitlines(keepends=True)
    if timed_out and lines and not lines[-1].endswith(b'\n'):
        lines.pop()  # Only an interrupted final line can be incomplete.
    worker_code, result = None, None
    for line in lines:
        require(result is None and line.endswith(b'\n'))
        item = json.loads(line, object_pairs_hook=sina._unique_pairs, parse_constant=sina._reject_constant)
        require(isinstance(item, dict))
        kind = item.get('kind')
        if worker_code is None:
            require(kind == 'identity' and item.keys() == {'kind', 'identity'})
            worker_code = _validate_identity(item['identity'])
        elif kind == 'stage':
            require(item.keys() == {'kind', 'stage', 'check', 'state', 'metrics'})
            require(item['state'] in ('running', 'passed', 'failed'))
            _metrics(item['metrics'])
            if item['state'] == 'running':
                trace.begin(item['stage'], item['check'], **item['metrics'])
            else:
                require(trace.items and trace.items[-1]['stage'] == item['stage'] and
                        trace.items[-1]['check'] == item['check'] and trace.items[-1]['metrics'] == item['metrics'])
                if item['state'] == 'passed':
                    trace.passed(item['stage'])
                else:
                    require(trace.items[-1]['state'] == 'running')
                    trace.failed()
        elif kind == 'result':
            require(item.keys() == {'kind', 'status', 'error_type'})
            require(item['status'] in ('observed', 'unavailable'))
            require(item['error_type'] is None or item['error_type'] in ERRORS)
            if item['status'] == 'observed':
                require(item['error_type'] is None and [i['stage'] for i in trace.items] == list(STAGES)
                        and all(i['state'] == 'passed' for i in trace.items))
            else:
                require(item['error_type'] in ERRORS and trace.items and trace.items[-1]['state'] == 'failed')
            result = item
        else:
            raise sina.SinaResponseError()
    return worker_code, result


def diagnose(expected_last_date=None, *, runner=subprocess.run, now=None):
    code, trace = identity(), Trace()
    worker_matches = None
    phase, timed_out = 'input', False
    try:
        stamp = market._clock(now)
        expected, basis = market.expected_daily_date(stamp, expected_last_date)
        begin, expected = str(market.three_years_ago(stamp.astimezone(market.BJ).date())), str(expected)
        command = [sys.executable, '-m', 'goose_research.diagnose_sina', '--worker', begin, expected, basis]
        phase = 'launch'
        with tempfile.TemporaryFile() as output:
            try:
                completed = runner(command, cwd=str(Path(__file__).resolve().parents[1]), stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.DEVNULL, timeout=sina.WORKER_TIMEOUT, check=False)
            except subprocess.TimeoutExpired:
                timed_out = True
            phase = 'protocol'
            require(os.fstat(output.fileno()).st_size <= MAX_PROTOCOL)
            output.seek(0)
            raw = output.read(MAX_PROTOCOL + 1)
        phase = 'protocol'
        worker_code, result = _consume(raw, trace, timed_out=timed_out)
        worker_matches = worker_code == code if worker_code is not None else None
        if worker_matches is False:
            trace.begin('worker', 'code_identity')
            raise sina.SinaResponseError()
        if timed_out:
            if not trace.items or trace.items[-1]['state'] != 'running':
                trace.begin('worker', 'timeout')
            raise sina.SinaTimeoutError()
        require(worker_matches is True and result is not None)
        require((completed.returncode == 0) == (result['status'] == 'observed'))
        return _report(code, trace, result['status'], result['error_type'], worker_matches)
    except Exception as exc:
        if phase == 'input':
            trace.begin('input', 'dates')
        elif isinstance(exc, sina.SinaTimeoutError) and timed_out:
            pass  # Preserve the last running phase from the flushed progress.
        elif not trace.items or trace.items[-1]['stage'] != 'worker':
            trace.begin('worker', 'launch' if phase == 'launch' else 'protocol')
        if trace.items[-1]['state'] != 'failed':
            trace.failed()
        return _report(code, trace, 'unavailable', _error_type(exc), worker_matches)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise sina.SinaResponseError()


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '--worker':
        return _worker(argv[1:])
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
    try:
        parser = _Parser(description='仅输出脱敏阶段诊断，不保存行情或读取持仓。')
        parser.add_argument('--expected-last-date')
        args = parser.parse_args(argv)
        report = diagnose(args.expected_last_date)
    except Exception:
        trace = Trace()
        trace.begin('input', 'dates')
        trace.failed()
        report = _report(identity(), trace, 'unavailable', 'SinaResponseError')
    print(json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2))
    return 0 if report['status'] == 'observed' else 1


if __name__ == '__main__':
    raise SystemExit(_main())
