"""Bounded explicit HTTPS observations, preserving OS proxy and CA policy.

Provider timestamps are minute labels or quote-update times, never certified
last-trade timestamps. No fallback, retry, JavaScript evaluation or HTTP downgrade.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import http.client
import json
import re
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener, getproxies, proxy_bypass

from goose_research.market import BJ
from .json_limits import check_nesting

SYMBOL = '600900.SH'
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 8
SOURCES = {
    'eastmoney-minute': 'https://push2his.eastmoney.com/api/qt/stock/trends2/get',
    'tencent-quote': 'https://qt.gtimg.cn/q=sh600900',
}


def fail(code):
    raise ValueError(code)


def decimal_text(value, *, zero=False, tick=True):
    # Price magnitudes and volume/turnover magnitudes have different bounds.
    # A valid single-minute CNY turnover can exceed one hundred million.
    digits = 7 if tick else 17
    pattern = rf'(?:0|[1-9]\d{{0,{digits}}})(?:\.\d{{1,6}})?'
    if type(value) not in (str, int, Decimal) or not re.fullmatch(pattern, str(value)):
        fail('INVALID_DECIMAL_FIELD')
    number = Decimal(str(value))
    if number < 0 or (not zero and number == 0) or (tick and number % Decimal('0.01')):
        fail('PRICE_NONPOSITIVE_OR_TICK_INVALID')
    return format(number, 'f')


def lots(value):
    text = decimal_text(value, zero=True, tick=False)
    number = Decimal(text)
    if number != number.to_integral_value() or number > 10**12:
        fail('VOLUME_LOTS_INVALID')
    return int(number) * 100


def strict_json(raw):
    if len(raw) > MAX_BYTES:
        fail('UPSTREAM_BODY_TOO_LARGE')
    def unique(items):
        result = {}
        for key, value in items:
            if key in result or key in {'__proto__', 'prototype', 'constructor'}:
                fail('UPSTREAM_DUPLICATE_OR_UNSAFE_KEY')
            result[key] = value
        return result
    try:
        text = raw.decode('utf-8-sig')
        check_nesting(text)
        return json.loads(text, parse_float=Decimal,
            parse_constant=lambda _: fail('UPSTREAM_NONFINITE_NUMBER'), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        fail('UPSTREAM_JSON_INVALID')


def quote_base(source, name, price, previous, stamp, volume, kind):
    if name != '长江电力':
        fail('INSTRUMENT_NAME_MISMATCH')
    price, previous = decimal_text(price), decimal_text(previous)
    if abs(Decimal(price) / Decimal(previous) - 1) > Decimal('0.20'):
        fail('PRICE_SCALE_OR_LARGE_CHANGE_REQUIRES_REVIEW')
    return {'symbol': SYMBOL, 'source': source, 'sourceUrl': SOURCES[source], 'name': name,
        'price': price, 'previousClose': previous, 'currency': 'CNY', 'priceUnit': 'CNY-per-share',
        'adjustment': 'none', 'sourceTimestamp': stamp, 'timestampKind': kind,
        'confirmedLastTradeAt': None, 'volume': volume, 'volumeUnit': 'shares',
        'unitBasis': 'reviewed-adapter-field-semantics-not-independent-exchange-certification',
        'independentFeedVerified': False, 'warnings': ['LAST_TRADE_TIME_NOT_PROVIDED',
            'PUBLIC_WEB_ENDPOINT_NO_SCHEMA_OR_LATENCY_GUARANTEE']}


def parse_eastmoney(raw):
    doc = strict_json(raw)
    if type(doc) is not dict or type(doc.get('rc')) is not int or doc['rc'] != 0:
        fail('EASTMONEY_RETURN_CODE_INVALID')
    data = doc.get('data')
    if type(data) is not dict or data.get('code') != '600900' or type(data.get('market')) is not int or data['market'] != 1 or type(data.get('decimal')) is not int or data['decimal'] != 2:
        fail('EASTMONEY_IDENTITY_OR_DECIMAL_MISMATCH')
    rows = data.get('trends')
    if type(rows) is not list or not 1 <= len(rows) <= 2000:
        fail('EASTMONEY_EMPTY_OR_TOO_MANY_ROWS')
    previous_stamp, last, zero_open, historical_zero_open = None, None, False, 0
    for index, row in enumerate(rows):
        if type(row) is not str or len(row) > 400:
            fail('EASTMONEY_ROW_INVALID')
        values = row.split(',')
        if len(values) != 8 or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', values[0]):
            fail('EASTMONEY_ROW_FIELDS_INVALID')
        try:
            stamp = datetime.strptime(values[0], '%Y-%m-%d %H:%M').replace(tzinfo=BJ)
        except ValueError:
            fail('SOURCE_TIME_INVALID')
        if previous_stamp is not None and stamp <= previous_stamp:
            fail('MINUTE_TIMES_DUPLICATE_OR_UNSORTED')
        previous_stamp = stamp
        opening = decimal_text(values[1], zero=True)
        close, high, low = [decimal_text(v) for v in values[2:5]]
        if not Decimal(low) <= Decimal(close) <= Decimal(high):
            fail('MINUTE_OHLC_INVALID')
        if Decimal(opening) == 0:
            if stamp.strftime('%H:%M') != '09:30':
                if index == len(rows) - 1:
                    fail('LATEST_MINUTE_OPEN_MISSING_REQUIRES_REVIEW')
                historical_zero_open += 1
            zero_open = True  # Missing source open is not filled or used for trading.
        elif not Decimal(low) <= Decimal(opening) <= Decimal(high):
            fail('MINUTE_OHLC_INVALID')
        volume = lots(values[5])
        decimal_text(values[6], zero=True, tick=False)
        decimal_text(values[7], zero=True, tick=False)
        last = (stamp, close, volume)
    q = quote_base('eastmoney-minute', data.get('name'), last[1], data.get('prePrice'),
        last[0].isoformat(), last[2], 'minute-label-not-certified-last-trade')
    q.update(rowCount=len(rows), firstSourceTimestamp=rows[0].split(',')[0],
        sourceFields=['时间', '开盘', '收盘', '最高', '最低', '成交量(手)', '成交额(元)', '均价'],
        sourceDates=sorted({row.split(' ')[0] for row in rows}),
        historicalMissingOpenRows=historical_zero_open, barFinality='not-certified')
    if zero_open:
        q['warnings'].append('ZERO_MINUTE_OPEN_IS_MISSING_NOT_ZERO_PRICE')
    if historical_zero_open:
        q['warnings'].append('HISTORICAL_MINUTE_OPENS_MISSING_NOT_USED_IN_QUOTE_OR_BACKTEST')
    return q


def parse_tencent(raw):
    try:
        text = raw.decode('gbk')
    except UnicodeError:
        fail('TENCENT_ENCODING_INVALID')
    match = re.fullmatch(r'v_sh600900="([^"\r\n]*)";\s*', text)
    if not match:
        fail('TENCENT_FRAMING_OR_SYMBOL_INVALID')
    fields = match[1].split('~')
    if not 49 <= len(fields) <= 120 or fields[0] != '1' or fields[2] != '600900':
        fail('TENCENT_FIELDS_OR_IDENTITY_INVALID')
    if not re.fullmatch(r'\d{14}', fields[30]):
        fail('SOURCE_TIME_INVALID')
    try:
        stamp = datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=BJ)
    except ValueError:
        fail('SOURCE_TIME_INVALID')
    opening, high, low = [decimal_text(fields[i]) for i in [5, 33, 34]]
    price = decimal_text(fields[3])
    if not Decimal(low) <= min(Decimal(opening), Decimal(price)) <= max(Decimal(opening), Decimal(price)) <= Decimal(high):
        fail('TENCENT_OHLC_INVALID')
    volume = lots(fields[6])
    if fields[36] and lots(fields[36]) != volume:
        fail('TENCENT_VOLUME_FIELDS_DISAGREE')
    q = quote_base('tencent-quote', fields[1], price, fields[4], stamp.isoformat(), volume,
        'provider-quote-update-not-certified-last-trade')
    q.update(sourceFieldCount=len(fields), sourceFields=['1:name', '2:code', '3:price(CNY)',
        '4:previousClose', '5:open', '6:volume(lots)', '30:providerUpdate', '33:high', '34:low', '36:volume(lots)'])
    q['warnings'].append('TENCENT_UNVERSIONED_WEB_SCHEMA_REQUIRES_MANUAL_REVIEW')
    return q


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def default_opener():
    return build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))


def error_code(error):
    if isinstance(error, HTTPError):
        return 'HTTP_' + str(error.code)
    reason = getattr(error, 'reason', error)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return 'TLS_CERTIFICATE_VERIFICATION_FAILED'
    if isinstance(reason, ssl.SSLError):
        return 'TLS_FAILURE'
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return 'NETWORK_TIMEOUT'
    if isinstance(reason, socket.gaierror):
        return 'DNS_FAILURE_PROXY_OR_ORIGIN'
    if isinstance(reason, ConnectionRefusedError):
        return 'CONNECTION_REFUSED_PROXY_OR_ORIGIN'
    if isinstance(reason, http.client.RemoteDisconnected):
        return 'REMOTE_DISCONNECTED_PROXY_OR_ORIGIN'
    # Do not infer which network hop failed from an unstructured error string.
    return 'NETWORK_FAILURE_PROXY_OR_ORIGIN_UNRESOLVED'


def fetch_observation(source, *, opener=None):
    if source not in SOURCES:
        fail('SOURCE_NOT_SUPPORTED_NO_FALLBACK')
    endpoint = SOURCES[source]
    url = endpoint
    if source == 'eastmoney-minute':
        url += '?' + urlencode({'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58', 'ndays': '5', 'iscr': '0', 'secid': '1.600900'})
    begin = time.perf_counter()
    proxies = getproxies()
    result = {'source': source, 'endpoint': endpoint, 'requestedAt': datetime.now(timezone.utc).isoformat(),
        'receivedAt': None, 'elapsedMs': None, 'httpStatus': None, 'headers': {}, 'quote': None,
        'rawSha256': None, 'rawBytes': None, 'errors': [], 'transport': {
            'osHttpsProxyConfigured': bool(proxies.get('https')),
            'osBypassForHost': bool(proxy_bypass(urlsplit(endpoint).hostname)),
            'tlsVerification': True, 'proxyConfigurationModified': False, 'attempts': 1}}
    raw = None
    try:
        transport = opener or default_opener()
        with transport.open(Request(url, headers={'User-Agent': 'GooseResearch/0.3 (personal research)', 'Accept': '*/*'}), timeout=TIMEOUT) as response:
            result['httpStatus'] = response.status
            if response.status != 200:
                fail('HTTP_STATUS_NOT_200')
            if response.geturl() != url:
                fail('UPSTREAM_REDIRECT_OR_URL_CHANGED')
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raw = None
                fail('UPSTREAM_BODY_TOO_LARGE')
            result['rawBytes'], result['rawSha256'] = len(raw), sha256(raw).hexdigest()
            result['headers'] = {k: (response.headers.get(k) or '')[:150] for k in ['Date', 'Age', 'Content-Type']}
            result['quote'] = (parse_eastmoney if source == 'eastmoney-minute' else parse_tencent)(raw)
    except ValueError as error:
        code = str(error)
        result['errors'] = [code if re.fullmatch(r'[A-Z0-9_]{1,100}', code) else 'UPSTREAM_FIELDS_INVALID']
    except (URLError, OSError, http.client.HTTPException) as error:
        result['errors'] = [error_code(error)]
        if isinstance(error, HTTPError):
            result['httpStatus'] = error.code
    finally:
        result['receivedAt'] = datetime.now(timezone.utc).isoformat()
        result['elapsedMs'] = round((time.perf_counter() - begin) * 1000)
    return result, raw
