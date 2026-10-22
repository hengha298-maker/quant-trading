"""Shared trust boundary for CSV snapshots, provider data and saved JSON reports.

Validators never echo input values. Malformed documents are rejected, not repaired
or silently sorted. This checks consistency, not authenticity of user-entered data.
"""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import re

BJ = timezone(timedelta(hours=8))


def require(condition, message='数据结构或字段无效'):
    if not condition:
        raise ValueError(message)


def fields(obj, required, optional=()):
    require(isinstance(obj, dict))
    require(set(required) <= obj.keys() <= set(required) | set(optional), '缺少必要字段或含未知字段')


def text(value, limit=128):
    require(isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 for c in value))
    return value


def number(value, integer=False, signed=False):
    require(type(value) in (str, int, float) and len(str(value)) <= 128, '数值类型无效')
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('数值无效') from None
    require(n.is_finite(), '数值必须有限')
    require(-12 <= n.as_tuple().exponent <= 15 and abs(n) <= Decimal('1e15'), '数值超出支持范围或精度')
    require((signed or n >= 0) and (not integer or n == n.to_integral_value()), '数值符号或整数约束不满足')
    return n


def symbol(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]{6}\.(SH|SZ)', value), '证券代码无效')
    return value


def day(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value), '日期格式无效')
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise ValueError('日期无效') from None
    require(d <= datetime.now(BJ).date(), '不接受未来日期')
    return d


def version(document):
    require(type(document.get('schema_version')) is int and document['schema_version'] == 1, '不支持的数据版本')


def validate_snapshot(doc):
    fields(doc, ('schema_version', 'as_of', 'accounts', 'transaction_history_complete'))
    version(doc)
    day(doc['as_of'])
    require(doc['transaction_history_complete'] is False, 'P1不支持完整流水收益分析')
    require(isinstance(doc['accounts'], list) and 0 < len(doc['accounts']) <= 1000)
    seen = set()
    kinds = {}
    for a in doc['accounts']:
        fields(a, ('account', 'broker', 'coverage', 'cash', 'positions'))
        require(isinstance(a['account'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,32}', a['account']))
        require(a['account'] not in seen, '账户重复')
        seen.add(a['account'])
        text(a['broker'])
        require(a['coverage'] in ('partial', 'complete'))
        require(a['cash'] is not None or a['coverage'] == 'partial', '完整账户必须有现金数据')
        if a['cash'] is not None:
            number(a['cash'])
        require(isinstance(a['positions'], list) and len(a['positions']) <= 10000)
        positions = set()
        for p in a['positions']:
            fields(p, ('symbol', 'kind', 'quantity', 'available', 'broker_cost', 'cost_basis'))
            s = symbol(p['symbol'])
            require(s not in positions, '持仓重复')
            positions.add(s)
            require(p['kind'] in ('stock', 'etf') and p['cost_basis'] == 'broker_display')
            require(kinds.setdefault(s, p['kind']) == p['kind'], '同一证券的资产类型不一致')
            require(type(p['quantity']) is int and type(p['available']) is int, '持仓数量必须整数')
            q, available = number(p['quantity'], True), number(p['available'], True)
            require(q > 0 and available <= q, '持仓总量与可卖数量不一致')
            if p['broker_cost'] is not None:
                number(p['broker_cost'], signed=True)
    return doc


def timestamp(value, interval):
    fmt = '%Y-%m-%d' if interval == 'daily' else '%Y-%m-%d %H:%M'
    require(isinstance(value, str))
    try:
        stamp = datetime.strptime(value, fmt).replace(tzinfo=BJ)
    except ValueError:
        raise ValueError('行情时间格式无效') from None
    require(stamp.strftime(fmt) == value, '行情时间格式无效')
    require(stamp <= datetime.now(BJ), '不接受未来行情')
    return stamp


def validate_market(doc):
    fields(doc, ('schema_version', 'symbol', 'interval', 'source', 'adjustment', 'fetched_at',
                 'requested_start', 'requested_end', 'status', 'rows'),
           ('url', 'audit', 'audit_detail', 'error_type', 'count', 'observed_start', 'observed_end', 'stale',
            'provider_attempts', 'freshness', 'provenance'))
    version(doc)
    symbol(doc['symbol'])
    require(doc['interval'] in ('daily', 'minute') and doc['status'] in ('observed', 'unavailable'))
    text(doc['source'])
    require(doc['adjustment'] in ('不复权', '源站原始价格（复权口径未核验）'))
    begin, end = day(doc['requested_start']), day(doc['requested_end'])
    require(begin <= end, '请求日期范围无效')
    try:
        fetched = datetime.fromisoformat(text(doc['fetched_at']))
    except ValueError:
        raise ValueError('采集时间无效') from None
    require(fetched.tzinfo is not None and fetched <= datetime.now(timezone.utc), '采集时间必须含时区且不在未来')
    require(end <= fetched.astimezone(BJ).date(), '请求结束日期晚于采集日期')
    for key in ('url', 'audit', 'error_type'):
        if key in doc:
            text(doc[key], 4096)
    rows = doc['rows']
    require(isinstance(rows, list) and len(rows) <= 100000)
    if 'provider_attempts' in doc:
        attempts = doc['provider_attempts']
        require(isinstance(attempts, list) and 1 <= len(attempts) <= 2)
        seen = set()
        for attempt in attempts:
            fields(attempt, ('provider', 'status'), ('error_type',))
            require(attempt['provider'] in ('eastmoney', 'sina') and attempt['provider'] not in seen)
            seen.add(attempt['provider'])
            require(attempt['status'] in ('observed', 'unavailable', 'skipped'))
            if attempt['status'] == 'observed':
                require('error_type' not in attempt)
            else:
                require(isinstance(attempt.get('error_type'), str) and
                        re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', attempt['error_type']))
            if attempt['provider'] == 'sina':
                require(doc['symbol'] == '515450.SH' and doc['interval'] == 'daily')
            if attempt['status'] == 'skipped':
                require(attempt['provider'] == 'sina' and attempt['error_type'] == 'SourceNotVerified')
        require(len(attempts) == 1 or [a['provider'] for a in attempts] == ['eastmoney', 'sina'])
        require((attempts[-1]['status'] == 'observed') == (doc['status'] == 'observed'))
        require(not any(a['status'] == 'observed' for a in attempts[:-1]))
        if doc['status'] == 'observed' and attempts[-1]['provider'] == 'sina':
            require('provenance' in doc, '备用来源必须保留来源与复权审计')
    if doc['source'] == '新浪公开日线（AKShare 固定解码器）' and doc['status'] == 'observed':
        require('provenance' in doc, '备用来源必须保留来源与复权审计')
    if doc['status'] == 'unavailable':
        require(not rows and not any(k in doc for k in ('observed_start', 'observed_end', 'stale', 'audit_detail', 'freshness', 'provenance')))
        require(doc['adjustment'] == '不复权')
        require('count' not in doc or type(doc['count']) is int and doc['count'] == 0)
        return doc
    require(bool(rows) and 'error_type' not in doc, '有效行情必须有数据且不能携带失败状态')
    previous = None
    for row in rows:
        fields(row, ('time', 'open', 'close', 'high', 'low', 'volume'))
        stamp = timestamp(row['time'], doc['interval'])
        require(begin <= stamp.date() <= end and stamp <= fetched, '行情超出请求范围或晚于采集时间')
        require(previous is None or stamp > previous, '行情必须严格升序且不能重复')
        previous = stamp
        o, c, h, low = [number(row[k]) for k in ('open', 'close', 'high', 'low')]
        require(min(o, c, h, low) > 0 and low <= min(o, c) <= max(o, c) <= h, '行情价格或OHLC关系无效')
        number(row['volume'])
    require(type(doc.get('count')) is int and doc['count'] == len(rows), '行情条数不一致')
    require(doc.get('observed_start') == rows[0]['time'] and doc.get('observed_end') == rows[-1]['time'], '实际行情范围不一致')
    require(type(doc.get('stale')) is bool and doc['stale'] == (previous.date() != fetched.astimezone(BJ).date()), '行情滞后标记不一致')
    if 'freshness' in doc:
        fresh = doc['freshness']
        fields(fresh, ('expected_last_date', 'latest_date', 'checked_at', 'basis', 'calendar_verified', 'state'))
        require(doc['interval'] == 'daily' and fresh['calendar_verified'] is False)
        require(fresh['state'] == 'meets_date_requirement' and fresh['checked_at'] == doc['fetched_at'])
        require(fresh['basis'] in ('user_confirmed_date_not_calendar_verification', 'conservative_weekday_not_exchange_calendar'))
        require(begin <= day(fresh['expected_last_date']) <= day(fresh['latest_date']) <= end)
        require(fresh['latest_date'] == rows[-1]['time'])
        if fresh['basis'] == 'conservative_weekday_not_exchange_calendar':
            local = fetched.astimezone(BJ)
            candidate = local.date() - timedelta(days=1 if local.hour < 16 else 0)
            while candidate.weekday() >= 5:
                candidate -= timedelta(days=1)
            require(fresh['expected_last_date'] == str(candidate), '日线时效要求与采集时点不一致')
        elif day(fresh['expected_last_date']) == fetched.astimezone(BJ).date():
            require(fetched.astimezone(BJ).hour >= 16, '不接受尚未结束的当日日线')
    if 'provenance' in doc:
        provenance = doc['provenance']
        fields(provenance, ('provider', 'symbol', 'response_sha256', 'decoder', 'raw_start', 'raw_end',
                            'raw_count', 'volume_unit', 'adjustment_verified', 'raw_order_verified'))
        require(provenance['provider'] == 'sina' and provenance['symbol'] == 'sh515450')
        require(doc['symbol'] == '515450.SH' and doc['interval'] == 'daily' and
                doc['source'] == '新浪公开日线（AKShare 固定解码器）')
        require(doc.get('url') == 'https://finance.sina.com.cn/realstock/company/sh515450/hisdata_klc2/klc_kl.js',
                '备用来源地址与证券不一致')
        require(doc['adjustment'] == '源站原始价格（复权口径未核验）')
        require(provenance['adjustment_verified'] is False and provenance['raw_order_verified'] is True)
        require(provenance['decoder'] == 'akshare-1.19.1')
        require(isinstance(provenance['response_sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', provenance['response_sha256']))
        require(type(provenance['raw_count']) is int and len(rows) <= provenance['raw_count'] <= 100000)
        raw_begin, raw_end = day(provenance['raw_start']), day(provenance['raw_end'])
        require(raw_begin <= raw_end and provenance['raw_count'] <= (raw_end - raw_begin).days + 1)
        require((provenance['raw_count'] == 1) == (raw_begin == raw_end))
        require(day(provenance['raw_start']) <= date.fromisoformat(rows[0]['time']) and
                date.fromisoformat(rows[-1]['time']) <= day(provenance['raw_end']) <= fetched.astimezone(BJ).date())
        require(provenance['volume_unit'] == '源站原始值（AKShare文档称手，未核验）')
        if 'provider_attempts' in doc:
            require(doc['provider_attempts'][-1] == {'provider': 'sina', 'status': 'observed'})
    if doc['adjustment'] != '不复权':
        require('provenance' in doc, '未核验复权口径必须有独立来源审计')
    if 'audit_detail' in doc:
        audit = doc['audit_detail']
        fields(audit, ('calendar_verified', 'missing_weekday_candidates', 'note', 'last_observation_lag_days'))
        require(audit['calendar_verified'] is False)
        text(audit['note'], 4096)
        require(type(audit['last_observation_lag_days']) is int and audit['last_observation_lag_days'] == (fetched.astimezone(BJ).date() - previous.date()).days)
        missing = audit['missing_weekday_candidates']
        require(isinstance(missing, list) and all(isinstance(d, str) for d in missing))
        require(missing == sorted(set(missing)), '审计日期必须升序且无重复')
        observed_days = {r['time'][:10] for r in rows}
        for d in missing:
            require(begin <= day(d) <= end and d not in observed_days and day(d).weekday() < 5)
        require(not missing or doc['interval'] == 'daily')
    return doc


def validate_document(doc, kind=None):
    require(isinstance(doc, dict), 'JSON根必须是对象')
    if kind == 'snapshot' or kind is None and 'accounts' in doc:
        return validate_snapshot(doc)
    if kind == 'market' or kind is None and 'rows' in doc:
        return validate_market(doc)
    raise ValueError('无法识别的数据文档类型')
