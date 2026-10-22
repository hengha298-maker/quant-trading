"""Optional, isolated Sina daily candidate; importing this module needs only stdlib.

This is not a default source or proof of real availability. Only 515450.SH is in
scope. No remote JavaScript is evaluated: a pinned, locally installed AKShare
decoder receives only the validated compressed string. Adjustment and volume
semantics remain unverified even when syntactically valid bars are returned.
"""
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import hashlib
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .validation import SINA_CODECS, day, fields, number, timestamp, validate_sina_encoding

URL = 'https://finance.sina.com.cn/realstock/company/sh515450/hisdata_klc2/klc_kl.js'
AKSHARE_VERSION = '1.19.1'
MAX_BYTES = 8_000_000
MAX_ROWS = 100_000
DOWNLOAD_TIMEOUT = 15
WORKER_TIMEOUT = 45
JS_TIMEOUT_MS = 5_000
JS_MEMORY_BYTES = 128 * 1024 * 1024
VOLUME_UNIT = '源站原始值（AKShare文档称手，未核验）'
ADJUSTMENT_NOTE = '未在客户端复权；服务端口径待核验'
ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'


class SinaError(ValueError):
    def __init__(self):
        super().__init__('新浪候选数据不可用')


class SinaDependencyError(SinaError):
    pass


class SinaTimeoutError(SinaError):
    pass


class SinaResponseError(SinaError):
    pass


class SinaEncodingError(SinaError):
    pass


class SinaDecodeError(SinaError):
    pass


class Download(NamedTuple):
    compressed: str
    text: str
    response_sha256: str
    encoding: dict


def _encoding_header(payload):
    # Sina's stream is NOT ordinary Base64. Each alphabet index contributes
    # six little-endian bits: first 12 bits select a decoder, next 6 XOR 63
    # give its version. The AKShare 1.19.1 d() dispatcher uses this exact rule.
    if not isinstance(payload, str) or len(payload) < 4 or re.fullmatch(r'[A-Za-z0-9+/]+', payload) is None:
        raise SinaEncodingError()
    return ALPHABET.index(payload[0]) | (ALPHABET.index(payload[1]) << 6), 63 ^ ALPHABET.index(payload[2])


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SinaResponseError()


def _bounds(symbol, begin, end):
    if symbol != '515450.SH':
        raise SinaResponseError()
    try:
        begin = str(begin) if type(begin) is date else begin
        end = str(end) if type(end) is date else end
        if day(begin) > day(end):
            raise SinaResponseError()
    except (ValueError, TypeError):
        raise SinaResponseError() from None
    return begin, end


def _download(opener=None):
    opener = opener or build_opener(_NoRedirect()).open
    request = Request(URL, headers={'User-Agent': 'GooseResearch-P1-candidate/1.0'})
    try:
        with opener(request, timeout=DOWNLOAD_TIMEOUT) as response:
            if response.geturl() != URL or response.status != 200:
                raise SinaResponseError()
            length = response.headers.get('Content-Length')
            if length is not None and (not re.fullmatch(r'[0-9]+', length) or int(length) > MAX_BYTES):
                raise SinaResponseError()
            raw = response.read(MAX_BYTES + 1)
            if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
                raise SinaResponseError()
            if length is not None and int(length) != len(raw):
                raise SinaResponseError()
    except SinaError:
        raise
    except TimeoutError:
        raise SinaTimeoutError() from None
    except (HTTPError, URLError, OSError, ValueError, TypeError):
        raise SinaResponseError() from None
    try:
        body = raw.decode('utf-8-sig')
    except UnicodeError:
        raise SinaResponseError() from None
    # Full-match rejects a different symbol, truncated wrappers and appended JS.
    match = re.fullmatch(r'\s*var\s+(KLC_K(?:L|2)_sh515450)\s*=\s*"([A-Za-z0-9+/]+)"\s*;\s*', body)
    if match is None:
        raise SinaResponseError()
    wrapper, compressed = match.groups()
    codec, version = _encoding_header(compressed)
    if (codec, version) != SINA_CODECS[wrapper]:
        raise SinaEncodingError()
    encoding = dict(wrapper=wrapper, codec=codec, version=version, response_bytes=len(raw))
    return Download(compressed, body, hashlib.sha256(raw).hexdigest(), encoding)


def _optional_runtime():
    try:
        if metadata.version('akshare') != AKSHARE_VERSION:
            raise SinaDependencyError()
        constants = importlib.import_module('akshare.stock.cons')
        runtime = importlib.import_module('py_mini_racer')
        script = constants.hk_js_decode
        if not isinstance(script, str) or not 0 < len(script) < 100_000:
            raise SinaDependencyError()
        return script, runtime.MiniRacer
    except SinaError:
        raise
    except Exception:
        raise SinaDependencyError() from None


def _decode_payload(payload, runtime_loader=None):
    script, factory = (runtime_loader or _optional_runtime)()
    try:
        engine = factory()
        engine.eval(script, timeout=JS_TIMEOUT_MS, max_memory=JS_MEMORY_BYTES)
        return engine.call('d', payload, timeout=JS_TIMEOUT_MS, max_memory=JS_MEMORY_BYTES)
    except Exception as exc:
        if type(exc).__name__ in ('JSTimeoutException', 'JSTimeout'):
            raise SinaTimeoutError() from None
        raise SinaDecodeError() from None


def _normalise_rows(decoded):
    if not isinstance(decoded, list) or not 0 < len(decoded) <= MAX_ROWS:
        raise SinaDecodeError()
    result, previous = [], None
    try:
        for item in decoded:
            fields(item, ('date', 'open', 'high', 'low', 'close', 'volume'),
                   ('prevclose', 'amount', 'postVol', 'postAmt'))
            # MiniRacer JSON serialization represents the decoder's UTC-midnight
            # daily Date objects this way; other time-of-day labels are rejected.
            value = item['date']
            if not isinstance(value, str) or not re.fullmatch(
                    r'[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T00:00:00(?:\.000)?Z)?', value):
                raise SinaDecodeError()
            stamp = value[:10]
            timestamp(stamp, 'daily')
            if previous is not None and stamp <= previous:
                raise SinaDecodeError()
            o, h, low, c = [number(item[k]) for k in ('open', 'high', 'low', 'close')]
            volume = number(item['volume'])
            if min(o, h, low, c) <= 0 or not low <= min(o, c) <= max(o, c) <= h:
                raise SinaDecodeError()
            for key in ('prevclose', 'amount', 'postVol', 'postAmt'):
                if key in item:
                    number(item[key])
            result.append(dict(time=stamp, open=str(o), high=str(h), low=str(low), close=str(c), volume=str(volume)))
            previous = stamp
    except SinaError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError):
        raise SinaDecodeError() from None
    return result


def _fetch_worker(symbol, begin, end, opener=None, decoder=None):
    begin, end = _bounds(symbol, begin, end)
    downloaded = _download(opener)
    rows = _normalise_rows((decoder or _decode_payload)(downloaded.compressed))
    # Validate the entire original history before clipping: an invalid old row
    # cannot disappear merely because it lies outside the requested interval.
    selected = [row for row in rows if begin <= row['time'] <= end]
    if not selected:
        raise SinaDecodeError()
    return dict(rows=selected, raw_start=rows[0]['time'], raw_end=rows[-1]['time'], raw_count=len(rows),
                response_sha256=downloaded.response_sha256, encoding=downloaded.encoding,
                requested_url=URL, decoder='akshare-' + AKSHARE_VERSION,
                volume_unit=VOLUME_UNIT, adjustment='unknown', adjustment_note=ADJUSTMENT_NOTE)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SinaResponseError()
        result[key] = value
    return result


def _reject_constant(_value):
    raise SinaResponseError()


def _validate_result(result, begin, end):
    try:
        fields(result, ('rows', 'raw_start', 'raw_end', 'raw_count', 'response_sha256',
                        'requested_url', 'decoder', 'volume_unit', 'adjustment', 'adjustment_note', 'encoding'))
        validate_sina_encoding(result['encoding'])
        if (result['requested_url'] != URL or result['decoder'] != 'akshare-' + AKSHARE_VERSION or
                result['volume_unit'] != VOLUME_UNIT or result['adjustment'] != 'unknown' or
                result['adjustment_note'] != ADJUSTMENT_NOTE or
                not isinstance(result['response_sha256'], str) or
                not re.fullmatch(r'[0-9a-f]{64}', result['response_sha256']) or
                type(result['raw_count']) is not int or not 0 < result['raw_count'] <= MAX_ROWS):
            raise SinaResponseError()
        first, last = day(result['raw_start']), day(result['raw_end'])
        if (first > last or result['raw_count'] > (last - first).days + 1 or
                (result['raw_count'] == 1) != (first == last) or
                not isinstance(result['rows'], list) or not 0 < len(result['rows']) <= result['raw_count']):
            raise SinaResponseError()
        previous = None
        for row in result['rows']:
            fields(row, ('time', 'open', 'high', 'low', 'close', 'volume'))
            d = timestamp(row['time'], 'daily').date()
            if not begin <= row['time'] <= end or not first <= d <= last or (previous is not None and row['time'] <= previous):
                raise SinaResponseError()
            if any(not isinstance(row[key], str) for key in ('open', 'high', 'low', 'close', 'volume')):
                raise SinaResponseError()
            o, h, low, c = [number(row[key]) for key in ('open', 'high', 'low', 'close')]
            number(row['volume'])
            if min(o, h, low, c) <= 0 or not low <= min(o, c) <= max(o, c) <= h:
                raise SinaResponseError()
            previous = row['time']
    except SinaError:
        raise
    except (ValueError, KeyError, TypeError, OverflowError):
        raise SinaResponseError() from None
    return result


def fetch_sina_rows(symbol, begin, end, runner=subprocess.run):
    """Fetch the optional candidate in a 45-second child process, never in V1.

    Child stdout is a bounded JSON protocol, not a diagnostic log. Temporary
    stdout is read with an 8 MB cap; stderr is discarded and never surfaced.
    Proxy variables and default TLS validation are inherited unchanged.
    """
    begin, end = _bounds(symbol, begin, end)
    command = [sys.executable, '-m', 'goose_research.sina', '--worker', 'sh515450', begin, end]
    try:
        with tempfile.TemporaryFile() as output:
            completed = runner(command, cwd=str(Path(__file__).resolve().parents[1]), stdin=subprocess.DEVNULL,
                               stdout=output, stderr=subprocess.DEVNULL, timeout=WORKER_TIMEOUT, check=False)
            if os.fstat(output.fileno()).st_size > MAX_BYTES:
                raise SinaResponseError()
            output.seek(0)
            raw = output.read(MAX_BYTES + 1)
            if not raw or len(raw) > MAX_BYTES:
                raise SinaResponseError()
    except subprocess.TimeoutExpired:
        raise SinaTimeoutError() from None
    except SinaError:
        raise
    except (OSError, ValueError, TypeError):
        raise SinaError() from None
    try:
        result = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        if completed.returncode != 0:
            errors = {'dependency': SinaDependencyError, 'timeout': SinaTimeoutError, 'encoding': SinaEncodingError,
                      'response': SinaResponseError, 'decode': SinaDecodeError}
            if isinstance(result, dict) and set(result) == {'error'} and result['error'] in errors:
                raise errors[result['error']]()
            raise SinaError()
        return _validate_result(result, begin, end)
    except SinaError:
        raise
    except (ValueError, UnicodeError, TypeError, KeyError):
        raise SinaResponseError() from None


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 4 or argv[:2] != ['--worker', 'sh515450']:
            raise SinaResponseError()
        # Optional libraries do not get a channel for incidental import/output.
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            result = _fetch_worker('515450.SH', argv[2], argv[3])
        raw = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
        if len(raw) > MAX_BYTES:
            raise SinaResponseError()
        sys.stdout.buffer.write(raw)
        return 0
    except Exception as exc:
        category = 'dependency' if isinstance(exc, SinaDependencyError) else 'timeout' if isinstance(exc, SinaTimeoutError) else 'encoding' if isinstance(exc, SinaEncodingError) else 'decode' if isinstance(exc, SinaDecodeError) else 'response'
        sys.stdout.buffer.write(json.dumps({'error': category}).encode('ascii'))
        return 1


if __name__ == '__main__':
    raise SystemExit(_main())
