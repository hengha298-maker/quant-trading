"""Public Eastmoney endpoint; no promise of historical minute coverage."""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import json
import re
from urllib.parse import urlencode
from urllib.request import urlopen
from .storage import save


def three_years_ago(today):
    try:
        return today.replace(year=today.year - 3)
    except ValueError:
        return today.replace(year=today.year - 3, day=28)


def fetch(symbol, interval, opener=urlopen):
    if not re.fullmatch(r'\d{6}\.(SH|SZ)', symbol) or interval not in ('daily', 'minute'):
        raise ValueError('行情参数无效')
    today = datetime.now(timezone(timedelta(hours=8))).date()
    begin = three_years_ago(today)
    params = dict(secid=('1.' if symbol.endswith('.SH') else '0.') + symbol[:6],
                  klt='101' if interval == 'daily' else '1', fqt='0',
                  beg=begin.strftime('%Y%m%d'), end=today.strftime('%Y%m%d'), lmt='1000',
                  fields1='f1,f2,f3,f4,f5,f6', fields2='f51,f52,f53,f54,f55,f56')
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?' + urlencode(params)
    result = dict(schema_version=1, symbol=symbol, interval=interval, source='东方财富公开接口',
                  adjustment='不复权', url=url, fetched_at=datetime.now(timezone.utc).isoformat(),
                  requested_start=str(begin), requested_end=str(today), status='unavailable', rows=[],
                  audit='仅审计实际返回范围；未核对交易所逐日历，不能宣称三年完整。分钟历史范围不承诺；不补造缺失行情。')
    try:
        with opener(url, timeout=15) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError('response too large')
        data = json.loads(raw)
        payload = data['data']
        if payload.get('code') != symbol[:6] or str(payload.get('market')) != params['secid'][0]:
            raise ValueError('symbol mismatch')
        rows = []
        seen = set()
        for line in payload['klines']:
            values = line.split(',')
            stamp = datetime.strptime(values[0], '%Y-%m-%d' if interval == 'daily' else '%Y-%m-%d %H:%M')
            day = stamp.date()
            nums = [Decimal(v) for v in values[1:6]]
            if len(nums) != 5 or any(not n.is_finite() for n in nums) or min(nums[:4]) <= 0 or nums[4] < 0:
                raise ValueError('invalid values')
            o, c, h, low, volume = nums
            if low > min(o, c) or h < max(o, c) or low > h or not begin <= day <= today or values[0] in seen:
                raise ValueError('invalid bar')
            seen.add(values[0])
            rows.append(dict(time=values[0], open=str(o), close=str(c), high=str(h), low=str(low), volume=str(volume)))
        rows.sort(key=lambda r: r['time'])
        if not rows:
            raise ValueError('empty data')
        days = {r['time'][:10] for r in rows}
        candidates = []
        cursor = begin
        while cursor <= today:
            if cursor.weekday() < 5 and str(cursor) not in days:
                candidates.append(str(cursor))
            cursor += timedelta(days=1)
        result['audit_detail'] = dict(calendar_verified=False, missing_weekday_candidates=candidates if interval == 'daily' else [],
                                      note='候选缺口含节假日、停牌、上市前日期；不能直接视为漏数。分钟不推算历史缺口。',
                                      last_observation_lag_days=(today - date.fromisoformat(rows[-1]['time'][:10])).days)
        result.update(status='observed', rows=rows, observed_start=rows[0]['time'], observed_end=rows[-1]['time'],
                      count=len(rows), stale=rows[-1]['time'][:10] != str(today))
    except Exception as exc:
        # Do not expose server response, arbitrary URL text or local credentials.
        result['error_type'] = type(exc).__name__
    return result


def collect(base, symbol, interval):
    return save(base, 'market', fetch(symbol, interval))
