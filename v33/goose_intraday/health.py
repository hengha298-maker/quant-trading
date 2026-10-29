"""Fail-closed observation checks; passing checks never authorizes a trade."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import re

from goose_research.market import BJ, xshg_sessions, validate_dataset, calendar_supported_through
from .network import SYMBOL, SOURCES, decimal_text

MAX_AGE_SECONDS = 90
MAX_RECEIPT_AGE_SECONDS = 30
MAX_REQUEST_MS = 8000
MAX_SOURCE_SKEW_SECONDS = 60
MAX_PRICE_DIFFERENCE_BPS = 10


def aware(value):
    if not isinstance(value, str) or not 20 <= len(value) <= 40 or 'T' not in value:
        raise ValueError('AWARE_TIMESTAMP_REQUIRED')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('SOURCE_TIME_INVALID') from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('AWARE_TIMESTAMP_REQUIRED')
    return stamp


def continuous(stamp):
    t = stamp.astimezone(BJ).time().replace(tzinfo=None)
    return time(9, 30) <= t <= time(11, 30) or time(13) <= t < time(14, 57)


def context(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('AWARE_CLOCK_REQUIRED')
    local = now.astimezone(BJ)
    day = local.date().isoformat()
    codes = []
    try:
        session = day in xshg_sessions(day, day)
        calendar = 'reviewed-through-'+calendar_supported_through()
    except ValueError:
        session, calendar = False, 'unverified-or-unavailable'
        codes.append('CALENDAR_UNVERIFIED_STOP')
    if calendar != 'unverified-or-unavailable' and not session:
        codes.append('NOT_AN_XSHG_SESSION')
    in_session = session and continuous(local)
    if not in_session:
        codes.append('OUTSIDE_CONTINUOUS_SESSION_INTRADAY_NOT_VERIFIED')
    manual = in_session and time(14, 35) <= local.time().replace(tzinfo=None) < time(14, 45)
    if not manual:
        codes.append('OUTSIDE_1435_1445_MANUAL_WINDOW')
    return {'date': day, 'localTime': local.isoformat(), 'calendar': calendar,
        'exchangeSession': session, 'continuousSession': in_session, 'manualWindow': manual,
        'codes': codes}


def inspect_observation(observation, now):
    source = observation.get('source') if type(observation) is dict else None
    if type(source) is not str:
        source = None
    output = {'source': source if source in SOURCES else 'unsupported', 'checksPassed': False,
        'sourceTimestamp': None, 'timestampKind': None, 'confirmedLastTradeAt': None,
        'price': None, 'currency': 'CNY', 'priceUnit': 'CNY-per-share',
        'ageSeconds': None, 'codes': [], 'warnings': ['LAST_TRADE_TIME_NOT_INDEPENDENTLY_VERIFIED']}
    codes = output['codes']
    if source not in SOURCES:
        codes.append('SOURCE_NOT_SUPPORTED'); return output
    output['httpStatus'] = observation.get('httpStatus') if type(observation.get('httpStatus')) is int and 100 <= observation['httpStatus'] <= 599 else None
    output['elapsedMs'] = observation.get('elapsedMs') if type(observation.get('elapsedMs')) is int and observation['elapsedMs'] >= 0 else None
    try:
        output['receivedAt'] = aware(observation.get('receivedAt')).isoformat()
    except ValueError:
        output['receivedAt'] = None
    errors = observation.get('errors')
    if type(errors) is not list:
        codes.append('OBSERVATION_SCHEMA_INVALID'); return output
    if errors:
        codes.extend(code if type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,100}', code) else 'UPSTREAM_ERROR_REDACTED' for code in errors[:10])
        return output
    quote = observation.get('quote')
    if type(quote) is not dict:
        codes.append('QUOTE_MISSING'); return output
    try:
        if observation.get('endpoint') != SOURCES[source] or quote.get('sourceUrl') != SOURCES[source] or quote.get('source') != source:
            raise ValueError('SOURCE_PROVENANCE_MISMATCH')
        if quote.get('symbol') != SYMBOL or quote.get('name') != '长江电力':
            raise ValueError('INSTRUMENT_IDENTITY_MISMATCH')
        if (quote.get('currency'), quote.get('priceUnit'), quote.get('adjustment')) != ('CNY', 'CNY-per-share', 'none'):
            raise ValueError('PRICE_UNIT_OR_ADJUSTMENT_MISMATCH')
        if quote.get('confirmedLastTradeAt') is not None or quote.get('independentFeedVerified') is not False:
            raise ValueError('UNSUPPORTED_TRADE_TIME_OR_INDEPENDENCE_CLAIM')
        expected_kind = 'minute-label-not-certified-last-trade' if source == 'eastmoney-minute' else 'provider-quote-update-not-certified-last-trade'
        if quote.get('timestampKind') != expected_kind:
            raise ValueError('TIMESTAMP_SEMANTICS_MISMATCH')
        if observation.get('httpStatus') != 200 or type(observation.get('elapsedMs')) is not int or not 0 <= observation['elapsedMs'] <= MAX_REQUEST_MS:
            raise ValueError('HTTP_OR_LATENCY_CHECK_FAILED')
        transport = observation.get('transport')
        if type(transport) is not dict or transport.get('tlsVerification') is not True or transport.get('proxyConfigurationModified') is not False or type(transport.get('attempts')) is not int or transport['attempts'] != 1:
            raise ValueError('TRANSPORT_SAFETY_POLICY_MISMATCH')
        if not isinstance(observation.get('rawSha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', observation['rawSha256']):
            raise ValueError('RAW_RESPONSE_FINGERPRINT_MISSING')
        price = decimal_text(quote.get('price'))
        previous = decimal_text(quote.get('previousClose'))
        if abs(Decimal(price) / Decimal(previous) - 1) > Decimal('0.20'):
            raise ValueError('PRICE_SCALE_OR_LARGE_CHANGE_REQUIRES_REVIEW')
        if type(quote.get('volume')) is not int or not 0 < quote['volume'] <= 10**14 or quote.get('volumeUnit') != 'shares':
            raise ValueError('NO_POSITIVE_VALID_VOLUME')
        stamp, receipt, request = [aware(value) for value in [quote.get('sourceTimestamp'), observation.get('receivedAt'), observation.get('requestedAt')]]
        output.update(sourceTimestamp=stamp.isoformat(), timestampKind=expected_kind, price=price,
            receivedAt=receipt.isoformat(), rawSha256=observation['rawSha256'], httpStatus=200,
            elapsedMs=observation['elapsedMs'], volume=quote['volume'], volumeUnit='shares',
            volumeScope='one-minute' if source == 'eastmoney-minute' else 'cumulative-session')
        warnings = quote.get('warnings', [])
        if type(warnings) is list:
            output['warnings'].extend(w for w in warnings[:20] if type(w) is str and re.fullmatch(r'[A-Z0-9_]{1,100}', w))
        if request > receipt or receipt > now or stamp > receipt:
            codes.append('FUTURE_OR_REVERSED_CLOCK')
        if (now - receipt).total_seconds() > MAX_RECEIPT_AGE_SECONDS:
            codes.append('RECEIPT_TOO_OLD_RECHECK_REQUIRED')
        age = (now - stamp).total_seconds()
        output['ageSeconds'] = round(age, 3)
        if stamp.astimezone(BJ).date() != now.astimezone(BJ).date():
            codes.append('SOURCE_NOT_TODAY')
        if age < 0:
            codes.append('SOURCE_TIMESTAMP_FROM_FUTURE')
        if age > MAX_AGE_SECONDS:
            codes.append('SOURCE_STALE_OVER_90_SECONDS')
        day = stamp.astimezone(BJ).date().isoformat()
        if day not in xshg_sessions(day, day):
            codes.append('SOURCE_DATE_NOT_EXCHANGE_SESSION')
        if source == 'eastmoney-minute':
            dates = quote.get('sourceDates')
            if type(dates) is not list or not dates or dates != sorted(set(dates)) or len(dates) > 5 or dates[-1] != day:
                raise ValueError('MINUTE_SOURCE_DATES_INVALID')
            sessions = set(xshg_sessions(dates[0], day))
            if any(date not in sessions for date in dates):
                codes.append('MINUTE_CONTAINS_NON_SESSION_DATE')
        if not continuous(stamp):
            codes.append('SOURCE_TIME_OUTSIDE_CONTINUOUS_SESSION')
        headers = observation.get('headers')
        if type(headers) is not dict:
            raise ValueError('HTTP_HEADER_SCHEMA_INVALID')
        age_header = headers.get('Age', '')
        if age_header:
            if type(age_header) is not str or not re.fullmatch(r'\d{1,8}', age_header):
                raise ValueError('HTTP_CACHE_AGE_INVALID')
            if int(age_header) > MAX_AGE_SECONDS:
                codes.append('HTTP_CACHE_TOO_OLD')
        date_header = headers.get('Date', '')
        if date_header:
            try:
                server = parsedate_to_datetime(date_header)
                if server.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError, OverflowError):
                raise ValueError('HTTP_SERVER_DATE_INVALID') from None
            if abs((receipt - server).total_seconds()) > 300:
                codes.append('HTTP_SERVER_CLOCK_SKEW_OVER_300_SECONDS')
        output['checksPassed'] = not codes
    except (ValueError, TypeError, ArithmeticError) as error:
        # A closed diagnostic, never a raw driver exception or free upstream text.
        code = str(error)
        codes.append(code if re.fullmatch(r'[A-Z0-9_]{1,100}', code) else 'OBSERVATION_FIELDS_INVALID')
    return output


def compare_observations(items):
    result = {'evaluated': False, 'numericAgreement': None, 'withinPolicyTolerance': None,
        'differenceBps': None, 'timestampSkewSeconds': None, 'codes': [],
        'independentFeedVerified': False, 'toleranceBps': MAX_PRICE_DIFFERENCE_BPS,
        'maxTimestampSkewSeconds': MAX_SOURCE_SKEW_SECONDS,
        'limitation': '分钟标签与报价更新时间采样不同；数值接近不等于独立交易所源、逐笔成交或授权验证。'}
    if len(items) != 2 or {x['source'] for x in items} != set(SOURCES) or not all(x['checksPassed'] for x in items):
        result['codes'] = ['TWO_FRESH_DISTINCT_PROVIDER_OBSERVATIONS_REQUIRED']; return result
    a, b = items
    result['timestampSkewSeconds'] = abs((aware(a['sourceTimestamp']) - aware(b['sourceTimestamp'])).total_seconds())
    if result['timestampSkewSeconds'] > MAX_SOURCE_SKEW_SECONDS:
        result['codes'] = ['SOURCE_TIMESTAMP_SKEW_TOO_LARGE']; return result
    first, second = Decimal(a['price']), Decimal(b['price'])
    difference = abs(first-second) / min(first, second) * 10000
    result.update(evaluated=True, numericAgreement=first == second,
        withinPolicyTolerance=difference <= MAX_PRICE_DIFFERENCE_BPS,
        differenceBps=format(difference.quantize(Decimal('0.000001')), 'f'))
    if not result['withinPolicyTolerance']:
        result['codes'].append('SOURCE_PRICE_DISAGREEMENT_REQUIRES_REVIEW')
    return result


def history_context(dataset, day):
    """V1 compares the previous complete close to its inclusive 20-day MA.

    No intraday price is substituted for the previous close; this is not a cross.
    """
    if dataset is None:
        return {'available': False, 'codes': ['PREVIOUS_20_DAILY_BARS_NOT_PROVIDED']}
    try:
        data = validate_dataset(dataset)
        if data['symbol'] != SYMBOL or data['quality']['status'] != 'validated':
            raise ValueError('DAILY_CONTEXT_NOT_VALIDATED_600900')
        before = datetime.fromisoformat(day).date() - timedelta(days=45)
        required = [s for s in xshg_sessions(before.isoformat(), day) if s < day][-20:]
        if len(required) != 20 or [b['date'] for b in data['bars'][-20:]] != required:
            raise ValueError('DAILY_CONTEXT_MUST_END_AT_PREVIOUS_SESSION_WITH_20_BARS')
        prices = [Decimal(b['close']) for b in data['bars'][-20:]]
        mean = sum(prices) / 20
        return {'available': True, 'from': required[0], 'through': required[-1],
            'previousClose': str(prices[-1]), 'ma20': str(mean),
            'comparison': 'above' if prices[-1] > mean else 'below' if prices[-1] < mean else 'equal',
            'rule': 'previous-complete-close-versus-inclusive-MA20-not-crossover',
            'source': data['source'], 'codes': [],
            'limitation': '来源发布时间及企业行为未独立认证；历史比较不等于当日买卖授权。'}
    except (ValueError, KeyError, TypeError):
        return {'available': False, 'codes': ['DAILY_CONTEXT_INVALID_STALE_OR_NOT_PREVIOUS_SESSION']}


def build_health(observations, *, now=None, mode='live', dataset=None):
    clock = now or datetime.now(timezone.utc)
    current = context(clock)
    if mode not in {'live', 'replay', 'synthetic'} or type(observations) is not list or not 1 <= len(observations) <= 2:
        raise ValueError('HEALTH_MODE_OR_OBSERVATION_COUNT_INVALID')
    inspected = [inspect_observation(x, clock) for x in observations]
    if len({x['source'] for x in inspected}) != len(inspected):
        raise ValueError('DUPLICATE_SOURCE_NOT_INDEPENDENT_OBSERVATION')
    comparison = compare_observations(inspected)
    candidate = mode == 'live' and current['manualWindow'] and comparison['evaluated'] and comparison['withinPolicyTolerance']
    codes = list(current['codes'])
    if mode != 'live':
        codes.append('REPLAY_OR_SYNTHETIC_CANNOT_VERIFY_LIVE_MARKET')
    codes.extend(code for item in inspected for code in item['codes'])
    codes.extend(comparison['codes'])
    # All successful results still require manual field/timing checks and later
    # sustained-session evidence. This research stage never grants trade rights.
    status = 'fresh-observation' if mode == 'live' and current['continuousSession'] and comparison['evaluated'] and comparison['withinPolicyTolerance'] else 'intraday-not-verified'
    return {'format': 'goose-intraday-health', 'version': 1, 'symbol': SYMBOL,
        'generatedAt': clock.astimezone(timezone.utc).isoformat(), 'mode': mode,
        'status': status, 'calendarContext': current, 'observations': inspected,
        'comparison': comparison, 'manualWindowChecksPassed': bool(candidate),
        'manualAcceptanceVerified': False, 'intradayReliabilityVerified': False,
        'decisionAllowed': False, 'stance': 'watch',
        'historyContext': history_context(dataset, current['date']),
        'codes': list(dict.fromkeys(codes)),
        'limitations': ['本轮只有行情健康观察，不生成真实买卖信号。',
            '分钟标签/报价更新时间不是已认证的最后成交时间。',
            '90秒新鲜度、60秒源时间差及10bps价格差是保守研究阈值，不是供应商SLA。',
            '未证明两家接口拥有独立原始行情；数值一致不提升独立性。',
            '公开网页接口无版本/延迟/持续可用保证；不认证再分发权。',
            '无盘中人工验收及持续可用证据时，不称为已通过盘中可靠性验证。'],
        'notification': {'sent': False, 'authorizationRequiredAgain': True},
        'originalTaskModified': False, 'automaticTaskCreated': False, 'realTrading': False}
