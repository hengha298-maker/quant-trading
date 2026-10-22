"""Isolated public quotes; one provider per immutable observation batch.

Sina is deliberately gated until a real 515450 verification is recorded. The
separate probe can be run without holdings and never enables a source silently.
"""
from datetime import date, datetime, timezone, timedelta, time
from decimal import Decimal
import json
import re
from urllib.parse import urlencode
from urllib.request import urlopen
from .storage import save
from .validation import validate_market, day

BJ = timezone(timedelta(hours=8))
SINA_DAILY_VERIFIED = False
SINA_SOURCE = '新浪公开日线（AKShare 固定解码器）'
SINA_ADJUSTMENT = '源站原始价格（复权口径未核验）'


class StaleDailyData(ValueError):
    pass


def three_years_ago(today):
    try:
        return today.replace(year=today.year - 3)
    except ValueError:
        return today.replace(year=today.year - 3, day=28)


def expected_daily_date(now, explicit=None):
    """Conservative date requirement, NOT an exchange calendar.

    Before 16:00 we request the previous weekday, excluding unfinished daily
    bars. Holidays can cause a conservative rejection; an explicit, independently
    checked last session date can be supplied without claiming a verified calendar.
    """
    local = now.astimezone(BJ)
    if explicit is not None:
        target = day(explicit)
        if target > local.date() or (target == local.date() and local.time() < time(16)):
            raise ValueError('要求的日线日期尚未结束或晚于采集日期')
        return target, 'user_confirmed_date_not_calendar_verification'
    target = local.date()
    if local.time() < time(16):
        target -= timedelta(days=1)
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target, 'conservative_weekday_not_exchange_calendar'


def _clock(now):
    stamp = datetime.now(timezone.utc) if now is None else now
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ValueError('采集时钟必须含时区')
    return stamp.astimezone(timezone.utc)


def _base(symbol, interval, provider, begin, end, now):
    return dict(schema_version=1, symbol=symbol, interval=interval, source=provider,
                adjustment='不复权', fetched_at=_clock(now).isoformat(),
                requested_start=str(begin), requested_end=str(end),
                status='unavailable', rows=[],
                audit='单批只使用一个来源，不拼接；未核对交易所日历，不承诺三年完整；无交易建议。')


def _failure(result, exc, now):
    for key in ('audit_detail', 'count', 'stale', 'observed_start', 'observed_end', 'freshness', 'provenance'):
        result.pop(key, None)
    result.update(status='unavailable', rows=[], error_type=type(exc).__name__, fetched_at=_clock(now).isoformat())
    return result


def _observed(result, rows, now, expected, basis):
    fetched = _clock(now)
    if expected is not None and (not rows or rows[-1]['time'][:10] < str(expected)):
        raise StaleDailyData('日线未达到要求的截至日期')
    result.update(status='observed', rows=rows, fetched_at=fetched.isoformat(),
                  observed_start=rows[0]['time'], observed_end=rows[-1]['time'],
                  count=len(rows), stale=rows[-1]['time'][:10] != str(fetched.astimezone(BJ).date()))
    result.pop('error_type', None)
    days = {r['time'][:10] for r in rows}
    candidates = []
    cursor = date.fromisoformat(result['requested_start'])
    end = date.fromisoformat(result['requested_end'])
    while cursor <= end:
        if cursor.weekday() < 5 and str(cursor) not in days:
            candidates.append(str(cursor))
        cursor += timedelta(days=1)
    result['audit_detail'] = dict(calendar_verified=False,
        missing_weekday_candidates=candidates if result['interval'] == 'daily' else [],
        note='候选缺口含节假日、停牌、上市前；成交量保留本来源原值，不跨来源换算或拼接。',
        last_observation_lag_days=(fetched.astimezone(BJ).date() - date.fromisoformat(rows[-1]['time'][:10])).days)
    if expected is not None:
        result['freshness'] = dict(expected_last_date=str(expected), latest_date=rows[-1]['time'][:10],
            checked_at=fetched.isoformat(), basis=basis, calendar_verified=False,
            state='meets_date_requirement')
    return validate_market(result)


def _eastmoney(symbol, interval, opener, begin, end, now, expected, basis):
    params = dict(secid=('1.' if symbol.endswith('.SH') else '0.') + symbol[:6],
                  klt='101' if interval == 'daily' else '1', fqt='0',
                  beg=begin.strftime('%Y%m%d'), end=end.strftime('%Y%m%d'), lmt='1000',
                  fields1='f1,f2,f3,f4,f5,f6', fields2='f51,f52,f53,f54,f55,f56')
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?' + urlencode(params)
    result = _base(symbol, interval, '东方财富公开接口', begin, end, now)
    result['url'] = url
    try:
        with opener(url, timeout=15) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError('response too large')
        payload = json.loads(raw)['data']
        if payload.get('code') != symbol[:6] or str(payload.get('market')) != params['secid'][0]:
            raise ValueError('symbol mismatch')
        rows = []
        for line in payload['klines']:
            values = line.split(',')
            if len(values) != 6:
                raise ValueError('invalid row shape')
            stamp = datetime.strptime(values[0], '%Y-%m-%d' if interval == 'daily' else '%Y-%m-%d %H:%M')
            nums = [Decimal(v) for v in values[1:6]]
            if any(not n.is_finite() for n in nums) or min(nums[:4]) <= 0 or nums[4] < 0:
                raise ValueError('invalid values')
            o, c, h, low, volume = nums
            if low > min(o, c) or h < max(o, c) or low > h or not begin <= stamp.date() <= end:
                raise ValueError('invalid bar')
            rows.append(dict(time=values[0], open=str(o), close=str(c), high=str(h), low=str(low), volume=str(volume)))
        if not rows:
            raise ValueError('empty data')
        return _observed(result, rows, now, expected, basis)
    except Exception as exc:
        return validate_market(_failure(result, exc, now))


def _sina(symbol, begin, end, now, expected, basis, fetcher=None):
    result = _base(symbol, 'daily', SINA_SOURCE, begin, end, now)
    try:
        if fetcher is None:
            from .sina import fetch_sina_rows
            fetcher = fetch_sina_rows
        raw = fetcher(symbol, begin, end)
        result['adjustment'] = SINA_ADJUSTMENT
        result['url'] = raw['requested_url']
        result['provenance'] = dict(provider='sina', symbol='sh515450',
            response_sha256=raw['response_sha256'], decoder=raw['decoder'],
            raw_start=raw['raw_start'], raw_end=raw['raw_end'], raw_count=raw['raw_count'],
            volume_unit=raw['volume_unit'], adjustment_verified=False, raw_order_verified=True)
        if 'encoding' in raw:
            result['provenance']['encoding'] = raw['encoding']
        return _observed(result, raw['rows'], now, expected, basis)
    except Exception as exc:
        # Revert unverified adjustment label if no valid provenance remains.
        result['adjustment'] = '不复权'
        return validate_market(_failure(result, exc, now))


def fetch(symbol, interval, opener=urlopen, *, source='auto', expected_last_date=None, now=None, sina_fetcher=None):
    if not isinstance(symbol, str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ)', symbol) or interval not in ('daily', 'minute'):
        raise ValueError('行情参数无效')
    if source not in ('auto', 'eastmoney', 'sina') or (source == 'sina' and (symbol, interval) != ('515450.SH', 'daily')):
        raise ValueError('备用来源仅适用于515450.SH日线')
    stamp = _clock(now)
    begin = three_years_ago(stamp.astimezone(BJ).date())
    expected, basis = expected_daily_date(stamp, expected_last_date) if interval == 'daily' else (None, None)
    if interval == 'minute' and expected_last_date is not None:
        raise ValueError('分钟采集不接受日线截至日期')
    end = expected if expected is not None else stamp.astimezone(BJ).date()
    attempts = []
    if source != 'sina':
        primary = _eastmoney(symbol, interval, opener, begin, end, now, expected, basis)
        attempt = dict(provider='eastmoney', status=primary['status'])
        if primary['status'] == 'unavailable':
            attempt['error_type'] = primary['error_type']
        attempts.append(attempt)
        if primary['status'] == 'observed' or source == 'eastmoney' or (symbol, interval) != ('515450.SH', 'daily'):
            primary['provider_attempts'] = attempts
            return validate_market(primary)
    if not SINA_DAILY_VERIFIED:
        attempts.append(dict(provider='sina', status='skipped', error_type='SourceNotVerified'))
        result = _base(symbol, interval, '无可用来源（新浪尚未验收）', begin, end, now)
        result['error_type'] = 'SourceNotVerified'
    else:
        result = _sina(symbol, begin, end, now, expected, basis, sina_fetcher)
        attempt = dict(provider='sina', status=result['status'])
        if result['status'] == 'unavailable':
            attempt['error_type'] = result['error_type']
        attempts.append(attempt)
        if result['status'] == 'unavailable':
            result['source'] = '无可用来源'
            result['error_type'] = 'AllSourcesUnavailable'
    result['provider_attempts'] = attempts
    return validate_market(result)


def collect(base, symbol, interval, *, source='auto', expected_last_date=None):
    return save(base, 'market', fetch(symbol, interval, source=source, expected_last_date=expected_last_date))
