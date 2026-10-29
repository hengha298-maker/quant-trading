"""Independent, fail-closed daily market input; never imports a V1 trading module.

Network reads use the default OS proxy and certificate trust. A successful HTTP
response is not an independent price or corporate-action verification.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from zoneinfo import ZoneInfo

BJ = ZoneInfo("Asia/Shanghai")
CALENDAR_SUPPORTED_THROUGH = "2026-12-31"
MAX_BYTES = 8 * 1024 * 1024
MAX_SESSIONS = 10000
INSTRUMENTS = {"600900.SH": "stock", "515450.SH": "etf"}
EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TENCENT_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
DOC_URL = "https://akshare.akfamily.xyz/data/stock/stock.html"
_TOP = {"format", "version", "symbol", "kind", "source", "sourceUrl", "adjustment", "currency", "priceUnit", "fetchedAt", "calendar", "bars", "quality"}


def _fail(code: str) -> None:
    # Never include request URLs, raw upstream errors, environment or credentials.
    raise ValueError(code)


def _obj(value, required, label, optional=()):
    if type(value) is not dict or set(value) - set(required) - set(optional) or not set(required) <= set(value):
        _fail(label + "_FIELDS_INVALID")


def _text(value, label, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or re.search(r"[\x00-\x1f]", value):
        _fail(label + "_INVALID")


def _date(value, label="DATE") -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail(label + "_INVALID")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        _fail(label + "_INVALID")
    return result


def _stamp(value, label):
    if not isinstance(value, str) or len(value) > 40 or "T" not in value:
        _fail(label + "_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(label + "_INVALID")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(label + "_TIMEZONE_REQUIRED")
    return parsed


def _price(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d{0,14})(?:\.\d{1,12})?", value):
        _fail("PRICE_DECIMAL_TEXT_REQUIRED")
    number = Decimal(value)
    if not number.is_finite() or number <= 0:
        _fail("PRICE_MUST_BE_POSITIVE")
    return number


def _https(value):
    _text(value, "SOURCE_URL", 2000)
    try:
        parts = urlsplit(value)
        valid = parts.scheme == "https" and parts.hostname and not parts.username and not parts.password and not parts.fragment and parts.port in (None, 443)
    except ValueError:
        valid = False
    if not valid:
        _fail("SOURCE_HTTPS_REQUIRED")


def xshg_sessions(start, end):
    """Return exchange sessions, never approximate them with Monday-Friday.

    The locally audited calendar ends at 2026-12-31. A dependency upgrade does
    not silently extend that independently reviewed boundary.
    """
    begin, finish = _date(start, "START"), _date(end, "END")
    if begin > finish:
        _fail("DATE_RANGE_REVERSED")
    if end > CALENDAR_SUPPORTED_THROUGH:
        from .calendar_review import sessions as reviewed_sessions
        future=reviewed_sessions(max(start,'2027-01-01'),end)
        return (xshg_sessions(start,CALENDAR_SUPPORTED_THROUGH) if start<=CALENDAR_SUPPORTED_THROUGH else [])+future
    try:
        import exchange_calendars
    except ImportError:
        _fail("EXCHANGE_CALENDAR_DEPENDENCY_REQUIRED")
    try:
        cal = exchange_calendars.get_calendar("XSHG")
        if begin < cal.first_session.date() or finish > cal.last_session.date():
            _fail("CALENDAR_DEPENDENCY_RANGE_UNSUPPORTED")
        sessions = [stamp.date().isoformat() for stamp in cal.sessions_in_range(start, end)]
    except ValueError:
        raise
    except Exception:
        _fail("EXCHANGE_CALENDAR_UNAVAILABLE")
    if len(sessions) > MAX_SESSIONS:
        _fail("CALENDAR_RANGE_TOO_LARGE")
    return sessions


def calendar_supported_through():
    from .calendar_review import supported_through
    return supported_through()


def etf_anomaly_diagnostics(dataset):
    """Read-only red flags, not an ETF identity or unit-verification service.

    Daily absolute close changes >=20%, 10/100-fold (or inverse) scale jumps
    within 5%, and the reviewed 515450 CNY 0.001 tick are checked separately.
    Suspected scale errors are never corrected. Decimal display bps are rounded
    to six places; threshold comparisons use the unrounded Decimal values.
    """
    if type(dataset) is not dict or dataset.get("kind") not in ("stock", "etf"):
        _fail("ETF_DIAGNOSTIC_DATASET_INVALID")
    if dataset["kind"] != "etf":
        return {"maxAbsChangeBps": None, "largeChangeDates": [], "tickMismatchDates": [], "suspectedScaleDates": [], "status": "not-applicable", "warnings": ["ETF_DIAGNOSTICS_NOT_APPLICABLE"]}
    bars, quality = dataset.get("bars"), dataset.get("quality")
    if not isinstance(bars, list) or not bars or type(quality) is not dict or type(quality.get("unitVerified")) is not bool:
        _fail("ETF_DIAGNOSTIC_DATASET_INVALID")
    large, tick_bad, scales = [], [], []
    previous, previous_day, maximum = None, None, Decimal(0)
    with localcontext() as context:
        context.prec = 80
        for bar in bars:
            if type(bar) is not dict:
                _fail("ETF_DIAGNOSTIC_BAR_INVALID")
            day = bar.get("date"); _date(day, "ETF_BAR_DATE")
            if previous_day is not None and day <= previous_day:
                _fail("ETF_DIAGNOSTIC_DATES_UNSORTED_OR_DUPLICATE")
            prices = {field: _price(bar.get(field)) for field in ("open", "high", "low", "close")}
            if dataset.get("symbol") == "515450.SH" and any(price % Decimal("0.001") != 0 for price in prices.values()):
                tick_bad.append(day)
            close = prices["close"]
            if previous is not None:
                absolute_delta = abs(close - previous)
                maximum = max(maximum, absolute_delta / previous * 10000)
                if absolute_delta * 5 >= previous:
                    large.append(day)
                ratio = close / previous
                if any(abs(ratio - factor) <= factor * Decimal("0.05") for factor in (Decimal("10"), Decimal("100"), Decimal("0.1"), Decimal("0.01"))):
                    scales.append(day)
            previous, previous_day = close, day
        display_bps = format(maximum.quantize(Decimal("0.000001")), "f")
    warnings = ["NO_ANOMALY_DETECTED_IS_NOT_PRICE_UNIT_VERIFICATION"]
    if not quality["unitVerified"]:
        warnings.append("ETF_PRICE_UNIT_NOT_INDEPENDENTLY_VERIFIED")
    if large:
        warnings.append("ETF_LARGE_DAILY_PRICE_CHANGE_REQUIRES_REVIEW")
    if scales:
        warnings.append("ETF_SUSPECTED_PRICE_SCALE_DISCONTINUITY_NO_AUTOMATIC_REPAIR")
    if tick_bad:
        warnings.append("ETF_PRICE_TICK_MISMATCH")
    if quality.get("status") == "synthetic":
        warnings.append("SYNTHETIC_ENGINEERING_FIXTURE_NOT_REAL_MARKET_EVIDENCE")
    return {"maxAbsChangeBps": display_bps, "largeChangeDates": large, "tickMismatchDates": tick_bad, "suspectedScaleDates": scales, "status": "blocked" if not quality["unitVerified"] or large or tick_bad or scales else "no-anomaly-detected", "warnings": warnings}


def validate_dataset(doc):
    """Validate and copy a dataset without imputing, scaling or valuing prices.

    ``blocked`` is a structurally valid record of unusable input, not permission
    to backtest it. ``synthetic`` must remain visibly marked in every report.
    """
    _obj(doc, _TOP, "DATASET")
    if doc["format"] != "goose-market-dataset" or type(doc["version"]) is not int or doc["version"] != 1:
        _fail("DATASET_SCHEMA_UNSUPPORTED")
    if not isinstance(doc["symbol"], str) or not re.fullmatch(r"\d{6}\.(?:SH|SZ)", doc["symbol"]):
        _fail("SYMBOL_INVALID")
    if doc["kind"] not in ("stock", "etf") or doc["symbol"] in INSTRUMENTS and doc["kind"] != INSTRUMENTS[doc["symbol"]]:
        _fail("INSTRUMENT_KIND_INVALID")
    _text(doc["source"], "SOURCE", 300)
    _https(doc["sourceUrl"])
    if (doc["adjustment"], doc["currency"], doc["priceUnit"]) != ("none", "CNY", "CNY-per-share"):
        _fail("PRICE_BASIS_UNSUPPORTED")
    fetched = _stamp(doc["fetchedAt"], "FETCHED_AT")
    if fetched > datetime.now(timezone.utc):
        _fail("FETCHED_AT_FROM_FUTURE")
    _obj(doc["calendar"], {"name", "sessions", "supportedThrough"}, "CALENDAR")
    calendar = doc["calendar"]
    supported=calendar_supported_through()
    reviewed_boundaries={f'{y}-12-31' for y in range(2026,int(supported[:4])+1)}
    if calendar["name"] != "XSHG" or calendar["supportedThrough"] not in reviewed_boundaries:
        _fail("CALENDAR_BASIS_UNSUPPORTED")
    sessions = calendar["sessions"]
    if not isinstance(sessions, list) or not sessions or len(sessions) > MAX_SESSIONS:
        _fail("CALENDAR_SESSIONS_INVALID")
    for session in sessions:
        _date(session, "SESSION")
    if sessions != sorted(set(sessions)):
        _fail("CALENDAR_SESSIONS_DUPLICATE_OR_UNSORTED")
    if sessions[-1] > calendar['supportedThrough']:
        _fail("CALENDAR_RANGE_UNSUPPORTED_AFTER_2026_12_31")
    if sessions[-1] > fetched.astimezone(BJ).date().isoformat():
        _fail("SESSION_FROM_FUTURE")
    if xshg_sessions(sessions[0], sessions[-1]) != sessions:
        _fail("CALENDAR_SESSION_MISSING_OR_NOT_EXCHANGE_SESSION")
    bars = doc["bars"]
    if not isinstance(bars, list) or len(bars) != len(sessions):
        _fail("BARS_MISSING_TRADING_SESSION")
    bar_dates = []
    for bar in bars:
        _obj(bar, {"date", "open", "high", "low", "close", "volume", "publishedAt"}, "BAR")
        day = _date(bar["date"], "BAR_DATE")
        if day > fetched.astimezone(BJ).date():
            _fail("BAR_FROM_FUTURE")
        bar_dates.append(bar["date"])
        prices = {key: _price(bar[key]) for key in ("open", "high", "low", "close")}
        if not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]:
            _fail("OHLC_RANGE_INVALID")
        if type(bar["volume"]) is not int or not 0 <= bar["volume"] <= 10**18:
            _fail("VOLUME_INTEGER_SHARES_REQUIRED")
        if bar["publishedAt"] is not None:
            published = _stamp(bar["publishedAt"], "PUBLISHED_AT")
            if published > fetched or published.astimezone(BJ).date() < day:
                _fail("SOURCE_PUBLICATION_TIME_INVALID")
    if bar_dates != sessions:
        _fail("BAR_DATES_DUPLICATE_UNSORTED_OR_MISSING")
    quality = doc["quality"]
    _obj(quality, {"status", "crossValidated", "warnings", "unitVerified", "corporateActionsVerified"}, "QUALITY", {"unitEvidence"})
    if quality["status"] not in ("validated", "blocked", "synthetic"):
        _fail("QUALITY_STATUS_INVALID")
    for field in ("crossValidated", "unitVerified", "corporateActionsVerified"):
        if type(quality[field]) is not bool:
            _fail("QUALITY_FLAG_BOOLEAN_REQUIRED")
    if not isinstance(quality["warnings"], list) or len(quality["warnings"]) > 100:
        _fail("QUALITY_WARNINGS_INVALID")
    for warning in quality["warnings"]:
        _text(warning, "QUALITY_WARNING", 1000)
    if "unitEvidence" in quality:
        _text(quality["unitEvidence"], "UNIT_EVIDENCE", 2000)
    if quality["status"] == "validated" and not quality["unitVerified"]:
        _fail("VALIDATED_PRICE_UNIT_NOT_VERIFIED")
    if doc["symbol"] == "515450.SH" and quality["status"] == "validated" and (not quality["unitVerified"] or not quality.get("unitEvidence", "").strip()):
        _fail("ETF_515450_UNIT_EVIDENCE_REQUIRED")
    if doc["symbol"] == "515450.SH" and quality["status"] == "validated" and etf_anomaly_diagnostics(doc)["status"] == "blocked":
        _fail("ETF_515450_ANOMALY_REQUIRES_REVIEW")
    result = deepcopy(doc)
    warnings = result["quality"]["warnings"]
    def warn(code):
        if code not in warnings:
            warnings.append(code)
    if not quality["corporateActionsVerified"]:
        warn("CORPORATE_ACTIONS_UNVERIFIED_NO_TOTAL_RETURN_CLAIM")
    if any(bar["publishedAt"] is None for bar in bars):
        warn("SOURCE_PUBLICATION_TIME_UNKNOWN_USE_NEXT_SESSION_ONLY")
    if not quality["crossValidated"]:
        warn("NOT_INDEPENDENTLY_CROSS_VALIDATED")
    if quality["status"] == "synthetic":
        warn("SYNTHETIC_ENGINEERING_FIXTURE_NOT_REAL_MARKET_EVIDENCE")
    if quality["status"] == "blocked":
        warn("BLOCKED_INPUT_NOT_USABLE_FOR_TRADING_OR_BACKTEST_CLAIMS")
    return result


class _SameHttpsOrigin(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname or new.port not in (None, 443) or new.username or new.password or new.fragment:
            _fail("NETWORK_REDIRECT_NOT_ALLOWED")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_json(url):
    """One bounded HTTPS request; default ProxyHandler preserves system policy."""
    opener = build_opener(_SameHttpsOrigin(), HTTPSHandler(context=ssl.create_default_context()))
    request = Request(url, headers={"User-Agent": "GooseResearch/0.1 (personal research)", "Accept": "application/json"})
    try:
        with opener.open(request, timeout=15) as response:
            if urlsplit(response.geturl()).scheme != "https":
                _fail("NETWORK_HTTPS_REQUIRED")
            data = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        _fail("NETWORK_HTTP_" + str(exc.code))
    except (URLError, OSError, ssl.SSLError):
        _fail("NETWORK_UNAVAILABLE_PROXY_OR_TLS_CHECK_REQUIRED")
    if len(data) > MAX_BYTES:
        _fail("UPSTREAM_RESPONSE_TOO_LARGE")
    try:
        text = data.decode("utf-8-sig").strip()
        if text.startswith("goose="):
            text = text[len("goose="):].rstrip(";")
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result or key in ("__proto__", "prototype", "constructor"):
                    _fail("UPSTREAM_DUPLICATE_OR_UNSAFE_KEY")
                result[key] = value
            return result
        return json.loads(text, parse_float=Decimal, parse_constant=lambda _value: _fail("UPSTREAM_NONFINITE_NUMBER"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError):
        _fail("UPSTREAM_JSON_INVALID")


def _volume(raw):
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        _fail("UPSTREAM_VOLUME_INVALID")
    if not value.is_finite() or value < 0 or value != value.to_integral_value():
        _fail("UPSTREAM_VOLUME_INVALID")
    return int(value) * 100  # Both adapters' documented raw lots -> integer shares, never price scaling.


def _eastmoney(symbol, start, end):
    code, exchange = symbol.split(".")
    query = {"secid": ("1" if exchange == "SH" else "0") + "." + code, "klt": "101", "fqt": "0", "beg": start.replace("-", ""), "end": end.replace("-", ""), "lmt": str(MAX_SESSIONS), "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"}
    data = _read_json(EASTMONEY_URL + "?" + urlencode(query))
    if type(data) is not dict or type(data.get("data")) is not dict or data["data"].get("code") != code or not isinstance(data["data"].get("klines"), list):
        _fail("EASTMONEY_RESPONSE_OR_SYMBOL_INVALID")
    result = []
    for line in data["data"]["klines"]:
        if not isinstance(line, str):
            _fail("EASTMONEY_ROW_INVALID")
        row = line.split(",")
        if len(row) < 6:
            _fail("EASTMONEY_ROW_INVALID")
        result.append({"date": row[0], "open": row[1], "close": row[2], "high": row[3], "low": row[4], "volume": _volume(row[5]), "publishedAt": None})
    return result


def _tencent(symbol, start, end):
    code, exchange = symbol.split(".")
    provider_symbol = exchange.lower() + code
    result = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        begin, finish = max(start, f"{year}-01-01"), min(end, f"{year}-12-31")
        query = {"_var": "goose", "param": f"{provider_symbol},day,{begin},{finish},640,"}
        doc = _read_json(TENCENT_URL + "?" + urlencode(query))
        try:
            rows = doc["data"][provider_symbol]["day"]
        except (KeyError, TypeError):
            _fail("TENCENT_UNADJUSTED_RESPONSE_OR_SYMBOL_INVALID")
        if not isinstance(rows, list):
            _fail("TENCENT_ROWS_INVALID")
        for row in rows:
            if not isinstance(row, list) or len(row) < 6 or not all(isinstance(value, str) for value in row[:5]):
                _fail("TENCENT_ROW_INVALID")
            result.append({"date": row[0], "open": row[1], "close": row[2], "high": row[3], "low": row[4], "volume": _volume(row[5]), "publishedAt": None})
    return result


def fetch_history(symbol, start, end, source="eastmoney", *, kind=None):
    """Fetch public daily history explicitly, with no automatic fallback.

    Other instruments require an explicit user-declared kind, remain blocked
    pending identity/unit verification, and are never guessed from a prefix.
    """
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(?:SH|SZ)", symbol):
        _fail("SYMBOL_INVALID")
    if symbol in INSTRUMENTS:
        if kind is not None and kind != INSTRUMENTS[symbol]:
            _fail("INSTRUMENT_KIND_CONFLICT")
        declared_kind = INSTRUMENTS[symbol]
    elif kind in ("stock", "etf"):
        declared_kind = kind
    else:
        _fail("UNREVIEWED_INSTRUMENT_EXPLICIT_KIND_REQUIRED")
    _date(start, "START"); _date(end, "END")
    if start > end:
        _fail("DATE_RANGE_REVERSED")
    if end > datetime.now(BJ).date().isoformat():
        _fail("FETCH_FUTURE_DATES_NOT_ALLOWED")
    if source not in ("eastmoney", "tencent"):
        _fail("SOURCE_NOT_SUPPORTED_NO_SILENT_FALLBACK")
    sessions = xshg_sessions(start, end)
    if not sessions:
        _fail("NO_EXCHANGE_SESSIONS")
    rows = (_eastmoney if source == "eastmoney" else _tencent)(symbol, start, end)
    selected = []
    for row in rows:
        _date(row["date"], "UPSTREAM_DATE")
        if row["date"] > datetime.now(BJ).date().isoformat():
            _fail("UPSTREAM_FUTURE_BAR")
        if start <= row["date"] <= end:
            selected.append(row)
    selected.sort(key=lambda value: value["date"])
    unit_verified = symbol == "600900.SH"
    warnings = [] if symbol in INSTRUMENTS else ["INSTRUMENT_KIND_USER_DECLARED_IDENTITY_AND_UNITS_NOT_VERIFIED"]
    evidence = f"{DOC_URL} ; unadjusted decimal price field, raw volume lots converted to shares; no independent cross-source price verification" if unit_verified else "Independent instrument identity/price-unit verification absent; prices retained verbatim without rescaling"
    doc = {"format": "goose-market-dataset", "version": 1, "symbol": symbol, "kind": declared_kind, "source": source, "sourceUrl": EASTMONEY_URL if source == "eastmoney" else TENCENT_URL, "adjustment": "none", "currency": "CNY", "priceUnit": "CNY-per-share", "fetchedAt": datetime.now(timezone.utc).isoformat(), "calendar": {"name": "XSHG", "sessions": sessions, "supportedThrough": calendar_supported_through()}, "bars": selected, "quality": {"status": "validated" if unit_verified else "blocked", "crossValidated": False, "warnings": warnings, "unitVerified": unit_verified, "corporateActionsVerified": False, "unitEvidence": evidence}}
    return validate_dataset(doc)
