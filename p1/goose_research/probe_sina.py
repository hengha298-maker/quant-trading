"""Manual, read-only Sina/API probe. No holdings, saved observations or gates.

The original AKShare API runs in the bounded worker against the same downloaded
compressed response. Its transport is replaced only inside that worker; prices
are never generated or replaced. This does not verify volume/adjustment semantics,
exchange-calendar coverage or any investment strategy.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch

from . import market, sina
from .validation import day, fields, number, validate_sina_encoding

API = 'akshare.fund_etf_hist_sina'
BASES = ('conservative_weekday_not_exchange_calendar', 'user_confirmed_date_not_calendar_verification')


SinaApiMismatchError = sina.SinaApiMismatchError


class SinaStaleDataError(sina.SinaError):
    pass


def _original_api():
    try:
        if sina.metadata.version('akshare') != sina.AKSHARE_VERSION:
            raise sina.SinaDependencyError()
        package = importlib.import_module('akshare')
        module = importlib.import_module('akshare.fund.fund_etf_sina')
        api = getattr(package, 'fund_etf_hist_sina', None)
        if not callable(api) or api is not module.fund_etf_hist_sina:
            raise sina.SinaDependencyError()
        return api, module.requests, module.py_mini_racer
    except sina.SinaError:
        raise
    except Exception:
        raise sina.SinaDependencyError() from None


def _verify_original_api(downloaded, raw_rows):
    compressed = downloaded.compressed
    api, transport, runtime = _original_api()
    static_script, factory = sina._optional_runtime()
    calls = {'get': 0, 'eval': 0, 'call': 0}

    def fixed_get(url, *args, **kwargs):
        if url != sina.URL or args or kwargs or calls['get']:
            raise sina.SinaResponseError()
        calls['get'] += 1
        # Preserve the EXACT downloaded body, including K2/KL wrapper. Renaming
        # the wrapper here would conceal API incompatibility with the real body.
        return type('DownloadedResponse', (), {'text': downloaded.text})()

    class BoundedMiniRacer:
        def __init__(self):
            self.engine = factory()

        def eval(self, script, *args, **kwargs):
            if script != static_script or args or kwargs or calls['eval']:
                raise sina.SinaDecodeError()
            calls['eval'] += 1
            return self.engine.eval(script, timeout=sina.JS_TIMEOUT_MS, max_memory=sina.JS_MEMORY_BYTES)

        def call(self, function, *args, **kwargs):
            if function != 'd' or args != (compressed,) or kwargs or calls['call']:
                raise sina.SinaDecodeError()
            calls['call'] += 1
            try:
                return self.engine.call('d', compressed, timeout=sina.JS_TIMEOUT_MS, max_memory=sina.JS_MEMORY_BYTES)
            finally:
                close = getattr(self.engine, 'close', None)
                if callable(close):
                    close()

    try:
        # No global transport change survives the isolated worker. There is no
        # fallback to the network if the API tries another URL or call pattern.
        with patch.object(transport, 'get', fixed_get), patch.object(runtime, 'MiniRacer', BoundedMiniRacer):
            frame = api(symbol='sh515450')
        if calls != {'get': 1, 'eval': 1, 'call': 1} or len(frame) != len(raw_rows):
            raise SinaApiMismatchError()
        records = frame.to_dict(orient='records')
        if not isinstance(records, list) or len(records) != len(raw_rows):
            raise SinaApiMismatchError()
        normalised = []
        for row in records:
            if not isinstance(row, dict) or type(row.get('date')) is not date:
                raise SinaApiMismatchError()
            fields(row, ('date', 'open', 'high', 'low', 'close', 'volume'), ('prevclose', 'amount', 'postVol', 'postAmt'))
            # Raw optional fields were validated where actually present. Pandas
            # fills absent optional keys (e.g. first-row-only prevclose) with
            # NaN when building columns. Compare only the required OHLCV, not
            # these synthetic missing values; never relax core numeric checks.
            copied = {key: row[key] for key in ('open', 'high', 'low', 'close', 'volume')}
            copied['date'] = row['date'].isoformat()
            normalised.append(copied)
        api_rows = sina._normalise_rows(normalised)
        for expected, actual in zip(raw_rows, api_rows):
            if expected['time'] != actual['time'] or any(
                    number(expected[key]) != number(actual[key]) for key in ('open', 'high', 'low', 'close', 'volume')):
                raise SinaApiMismatchError()
    except sina.SinaError:
        raise
    except Exception as exc:
        if type(exc).__name__ in ('JSTimeoutException', 'JSTimeout'):
            raise sina.SinaTimeoutError() from None
        raise SinaApiMismatchError() from None
    return True


def _probe_worker(begin, expected, basis, *, opener=None, decoder=None, verifier=None, now=None, diagnostic=None):
    sina._note(diagnostic, 'input', 'dates')
    begin, expected = sina._bounds('515450.SH', begin, expected)
    if basis not in BASES:
        raise sina.SinaResponseError()
    sina._passed(diagnostic, 'input')
    downloaded = sina._download(opener, diagnostic=diagnostic)
    sina._note(diagnostic, 'decode', 'payload')
    decoded = decoder(downloaded.compressed) if decoder else sina._decode_payload(downloaded.compressed, diagnostic=diagnostic)
    sina._note(diagnostic, 'decode', 'rows')
    raw_rows = sina._normalise_rows(decoded)
    sina._note(diagnostic, 'decode', 'rows', raw_count=len(raw_rows))
    sina._passed(diagnostic, 'decode')
    # The original unmodified sequence is validated before the API is allowed to
    # sort it. The API must then agree with every validated original row.
    sina._note(diagnostic, 'api', 'compare')
    if (verifier or _verify_original_api)(downloaded, raw_rows) is not True:
        raise SinaApiMismatchError()
    sina._passed(diagnostic, 'api')
    selected = [row for row in raw_rows if begin <= row['time'] <= expected]
    sina._note(diagnostic, 'freshness', 'last_date', expected_date=expected,
               latest_date=selected[-1]['time'] if selected else None, selected_count=len(selected))
    if not selected or selected[-1]['time'] != expected:
        raise SinaStaleDataError()
    sina._passed(diagnostic, 'freshness')
    return dict(status='observed', symbol='515450.SH', api_checked=API, api_rows_match=True,
                response_sha256=downloaded.response_sha256, encoding=downloaded.encoding,
                first_bar=selected[0], last_bar=selected[-1], raw_start=raw_rows[0]['time'], raw_end=raw_rows[-1]['time'],
                raw_count=len(raw_rows), selected_start=selected[0]['time'], selected_end=selected[-1]['time'],
                selected_count=len(selected), fetched_at=market._clock(now).isoformat(), expected_last_date=expected,
                date_basis=basis, requested_url=sina.URL, decoder='akshare-' + sina.AKSHARE_VERSION,
                volume_unit=sina.VOLUME_UNIT, adjustment='unknown', calendar_verified=False, gate_changed=False)


def _validate_summary(result, begin, expected, basis):
    try:
        fields(result, ('status', 'symbol', 'api_checked', 'api_rows_match', 'response_sha256', 'raw_start', 'raw_end',
                        'raw_count', 'selected_start', 'selected_end', 'selected_count', 'fetched_at', 'expected_last_date',
                        'date_basis', 'requested_url', 'decoder', 'volume_unit', 'adjustment', 'calendar_verified', 'gate_changed',
                        'encoding', 'first_bar', 'last_bar'))
        validate_sina_encoding(result['encoding'])
        if (result['status'] != 'observed' or result['symbol'] != '515450.SH' or result['api_checked'] != API or
                result['api_rows_match'] is not True or result['requested_url'] != sina.URL or
                result['decoder'] != 'akshare-' + sina.AKSHARE_VERSION or result['volume_unit'] != sina.VOLUME_UNIT or
                result['adjustment'] != 'unknown' or result['calendar_verified'] is not False or result['gate_changed'] is not False or
                result['expected_last_date'] != expected or result['date_basis'] != basis or
                not isinstance(result['response_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', result['response_sha256'])):
            raise sina.SinaResponseError()
        raw_first, raw_last = day(result['raw_start']), day(result['raw_end'])
        first, last = day(result['selected_start']), day(result['selected_end'])
        if (type(result['raw_count']) is not int or type(result['selected_count']) is not int or
                not 0 < result['selected_count'] <= result['raw_count'] <= sina.MAX_ROWS or
                not raw_first <= first <= last <= raw_last or
                result['raw_count'] > (raw_last - raw_first).days + 1 or
                result['selected_count'] > (last - first).days + 1 or
                not begin <= result['selected_start'] <= result['selected_end'] == expected):
            raise sina.SinaResponseError()
        fetched = datetime.fromisoformat(result['fetched_at'])
        if fetched.tzinfo is None or fetched > datetime.now(timezone.utc) or raw_last > fetched.astimezone(market.BJ).date():
            raise sina.SinaResponseError()
        for bar, stamp in ((result['first_bar'], result['selected_start']), (result['last_bar'], result['selected_end'])):
            fields(bar, ('time', 'open', 'high', 'low', 'close', 'volume'))
            if bar['time'] != stamp:
                raise sina.SinaResponseError()
            original = dict(bar)
            original['date'] = original.pop('time')
            if sina._normalise_rows([original])[0] != bar:
                raise sina.SinaResponseError()
        if (first == last) != (result['selected_count'] == 1) or (raw_first == raw_last) != (result['raw_count'] == 1):
            raise sina.SinaResponseError()
    except sina.SinaError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError):
        raise sina.SinaResponseError() from None
    return result


def probe(expected_last_date=None, *, runner=subprocess.run, now=None):
    """Manual probe with no private data directory and no persistence."""
    stamp = market._clock(now)
    expected, basis = market.expected_daily_date(stamp, expected_last_date)
    begin = str(market.three_years_ago(stamp.astimezone(market.BJ).date()))
    expected = str(expected)
    command = [sys.executable, '-m', 'goose_research.probe_sina', '--worker', begin, expected, basis]
    try:
        with tempfile.TemporaryFile() as output:
            completed = runner(command, cwd=str(Path(__file__).resolve().parents[1]), stdin=subprocess.DEVNULL,
                               stdout=output, stderr=subprocess.DEVNULL, timeout=sina.WORKER_TIMEOUT, check=False)
            if os.fstat(output.fileno()).st_size > sina.MAX_BYTES:
                raise sina.SinaResponseError()
            output.seek(0)
            raw = output.read(sina.MAX_BYTES + 1)
            if not raw or len(raw) > sina.MAX_BYTES:
                raise sina.SinaResponseError()
        result = json.loads(raw, object_pairs_hook=sina._unique_pairs, parse_constant=sina._reject_constant)
        if completed.returncode != 0:
            errors = {'dependency': sina.SinaDependencyError, 'timeout': sina.SinaTimeoutError, 'encoding': sina.SinaEncodingError,
                      'response': sina.SinaResponseError, 'decode': sina.SinaDecodeError,
                      'api_mismatch': SinaApiMismatchError, 'stale': SinaStaleDataError}
            if isinstance(result, dict) and set(result) == {'error'} and result['error'] in errors:
                raise errors[result['error']]()
            raise sina.SinaError()
        return _validate_summary(result, begin, expected, basis)
    except subprocess.TimeoutExpired:
        raise sina.SinaTimeoutError() from None
    except sina.SinaError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise sina.SinaResponseError() from None


def _worker_main(argv):
    try:
        if len(argv) != 4 or argv[0] != '--worker':
            raise sina.SinaResponseError()
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            result = _probe_worker(argv[1], argv[2], argv[3])
        raw = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
        if len(raw) > sina.MAX_BYTES:
            raise sina.SinaResponseError()
        sys.stdout.buffer.write(raw)
        return 0
    except Exception as exc:
        category = 'dependency' if isinstance(exc, sina.SinaDependencyError) else 'timeout' if isinstance(exc, sina.SinaTimeoutError) else 'encoding' if isinstance(exc, sina.SinaEncodingError) else 'api_mismatch' if isinstance(exc, SinaApiMismatchError) else 'stale' if isinstance(exc, SinaStaleDataError) else 'decode' if isinstance(exc, sina.SinaDecodeError) else 'response'
        sys.stdout.buffer.write(json.dumps({'error': category}).encode('ascii'))
        return 1


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise sina.SinaResponseError()


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '--worker':
        return _worker_main(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
    try:
        parser = _Parser(description='新浪515450日线独立核验；不读取持仓、不保存数据、不启用数据源。')
        parser.add_argument('--expected-last-date', metavar='YYYY-MM-DD', help='已独立核实的最后交易日；不代表完成交易所日历核验')
        args = parser.parse_args(argv)
        result = probe(args.expected_last_date)
        print('新浪日线独立核验：通过（仅响应绑定、结构、API一致性与截至日）')
        print('证券：515450.SH；核对API：' + result['api_checked'])
        encoding = result['encoding']
        print('响应格式：' + encoding['wrapper'] + '；编码：' + str(encoding['codec']) +
              '；版本：' + str(encoding['version']) + '；字节：' + str(encoding['response_bytes']))
        print('原始历史：' + result['raw_start'] + ' 至 ' + result['raw_end'] + '；' + str(result['raw_count']) + '条')
        print('选定区间：' + result['selected_start'] + ' 至 ' + result['selected_end'] + '；' + str(result['selected_count']) + '条')
        for label, bar in (('区间首日', result['first_bar']), ('区间末日', result['last_bar'])):
            print(label + '：' + bar['time'] + '；开/高/低/收：' + '/'.join(bar[k] for k in ('open', 'high', 'low', 'close')) +
                  '；成交量原值：' + bar['volume'])
        print('要求截至：' + result['expected_last_date'] + '；采集时间：' + result['fetched_at'])
        print('日期依据：' + ('用户指定日期，非完整日历核验' if result['date_basis'] == BASES[1] else '北京时间16点后工作日候选，非完整交易所日历'))
        print('响应SHA256：' + result['response_sha256'])
        print('来源：' + result['requested_url'])
        print('复权口径未核验；成交量：' + result['volume_unit'])
        print('未读持仓、未保存数据、未改变数据源门禁；不形成交易建议。')
        return 0
    except Exception as exc:
        allowed = (sina.SinaError, SinaApiMismatchError, SinaStaleDataError)
        name = type(exc).__name__ if isinstance(exc, allowed) else 'SinaResponseError'
        print('新浪日线独立核验：unavailable（' + name + '）')
        print('未取得通过核验的结果；未改变数据源门禁，未保存数据，不形成交易建议。')
        return 1


if __name__ == '__main__':
    raise SystemExit(_main())
