"""Read-only public market collection, reusing the reviewed V3.2/V3.3 boundary.

Every provider attempt is visible. OS proxies and TLS verification stay enabled.
Prices are verbatim Decimal strings; adjusted series never enter executable-price
backtests. Provider update times are not certified last-execution timestamps.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, time, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, getproxies, proxy_bypass

ROOT = Path(__file__).resolve().parents[1]
for relative in ("v32", "v33"):
    path = str(ROOT / relative)
    if path not in sys.path:
        sys.path.insert(0, path)
from goose_intraday.network import (default_opener, error_code, strict_json,
    parse_tencent as parse_stock_tencent, parse_eastmoney as parse_stock_eastmoney)
from goose_research.market import (BJ, CALENDAR_SUPPORTED_THROUGH, TENCENT_URL,
    EASTMONEY_URL, xshg_sessions, validate_dataset, etf_anomaly_diagnostics)

SPECS = {
    "600900.SH": {"kind": "stock", "tick": "0.01", "names": ("长江电力",)},
    "515450.SH": {"kind": "etf", "tick": "0.001", "names":
        ("红利低波ETF", "红利低波50ETF", "红利低波50ETF南方", "红利50")},
}
QUOTE_URL = "https://qt.gtimg.cn/q="
MINUTE_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 10
UNIT_REFERENCE = "https://www.sse.com.cn/assortment/fund/etf/question/c/c_20240118_5734754.shtml"


def _fail(code):
    raise ValueError(code)


def _decimal(value, *, tick=None, zero=False):
    if type(value) not in (str, int, Decimal) or not re.fullmatch(
            r"(?:0|[1-9]\d{0,15})(?:\.\d{1,8})?", str(value)):
        _fail("INVALID_DECIMAL_FIELD")
    number = Decimal(str(value))
    if number < 0 or not zero and number == 0:
        _fail("NONPOSITIVE_NUMBER")
    if tick is not None and number % Decimal(tick):
        _fail("PRICE_TICK_INVALID")
    return format(number, "f")


def _volume(value):
    number = Decimal(_decimal(value, zero=True))
    if number != number.to_integral_value() or number > 10**12:
        _fail("VOLUME_LOTS_INVALID")
    return int(number) * 100


def parse_tencent(raw, symbol):
    if symbol not in SPECS:
        _fail("INSTRUMENT_NOT_REVIEWED")
    if symbol == "600900.SH":
        return parse_stock_tencent(raw)
    try:
        text = raw.decode("gbk")
    except UnicodeError:
        _fail("TENCENT_ENCODING_INVALID")
    code, exchange = symbol.split(".")
    match = re.fullmatch(r'v_' + exchange.lower() + code + r'="([^"\r\n]*)";\s*', text)
    if not match:
        _fail("TENCENT_FRAMING_OR_SYMBOL_INVALID")
    fields = match[1].split("~")
    spec = SPECS[symbol]
    if not 49 <= len(fields) <= 120 or fields[0] != "1" or fields[2] != code or fields[1] not in spec["names"]:
        _fail("TENCENT_FIELDS_OR_IDENTITY_INVALID")
    if not re.fullmatch(r"\d{14}", fields[30]):
        _fail("SOURCE_TIME_INVALID")
    try:
        stamp = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=BJ)
    except ValueError:
        _fail("SOURCE_TIME_INVALID")
    price, previous, opening, high, low = [_decimal(fields[i], tick=spec["tick"]) for i in (3, 4, 5, 33, 34)]
    if not Decimal(low) <= min(Decimal(opening), Decimal(price)) <= max(Decimal(opening), Decimal(price)) <= Decimal(high):
        _fail("TENCENT_OHLC_INVALID")
    if abs(Decimal(price) / Decimal(previous) - 1) > Decimal("0.20"):
        _fail("PRICE_SCALE_OR_LARGE_CHANGE_REQUIRES_REVIEW")
    volume = _volume(fields[6])
    if fields[36] and _volume(fields[36]) != volume:
        _fail("TENCENT_VOLUME_FIELDS_DISAGREE")
    return {"symbol": symbol, "name": fields[1], "source": "tencent-quote",
        "sourceUrl": QUOTE_URL + exchange.lower() + code, "price": price,
        "previousClose": previous, "currency": "CNY", "priceUnit": "CNY-per-share",
        "tick": spec["tick"], "adjustment": "none", "sourceTimestamp": stamp.isoformat(),
        "timestampKind": "provider-quote-update-not-certified-last-trade", "confirmedLastTradeAt": None,
        "volume": volume, "volumeUnit": "shares", "sourceFieldCount": len(fields),
        "independentFeedVerified": False, "warnings": ["LAST_TRADE_TIME_NOT_PROVIDED",
            "PUBLIC_WEB_ENDPOINT_NO_SCHEMA_OR_LATENCY_GUARANTEE", "TENCENT_UNVERSIONED_WEB_SCHEMA_REQUIRES_MANUAL_REVIEW"]}


def parse_eastmoney(raw, symbol):
    if symbol == "600900.SH":
        return parse_stock_eastmoney(raw)
    if symbol not in SPECS:
        _fail("INSTRUMENT_NOT_REVIEWED")
    doc = strict_json(raw)
    data = doc.get("data") if type(doc) is dict else None
    spec = SPECS[symbol]
    if type(doc) is not dict or type(doc.get("rc")) is not int or doc["rc"] != 0 or type(data) is not dict:
        _fail("EASTMONEY_RETURN_CODE_INVALID")
    if (data.get("code") != symbol[:6] or type(data.get("market")) is not int or data["market"] != 1
            or type(data.get("decimal")) is not int or data["decimal"] != 3 or data.get("name") not in spec["names"]):
        _fail("EASTMONEY_IDENTITY_OR_DECIMAL_MISMATCH")
    rows = data.get("trends")
    if type(rows) is not list or not 1 <= len(rows) <= 2000:
        _fail("EASTMONEY_EMPTY_OR_TOO_MANY_ROWS")
    previous_stamp, last = None, None
    missing_opens = 0
    for index, row in enumerate(rows):
        if type(row) is not str or len(row) > 400:
            _fail("EASTMONEY_ROW_INVALID")
        values = row.split(",")
        if len(values) != 8 or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", values[0]):
            _fail("EASTMONEY_ROW_FIELDS_INVALID")
        stamp = datetime.strptime(values[0], "%Y-%m-%d %H:%M").replace(tzinfo=BJ)
        if previous_stamp and stamp <= previous_stamp:
            _fail("MINUTE_TIMES_DUPLICATE_OR_UNSORTED")
        previous_stamp = stamp
        opening = Decimal(_decimal(values[1], tick=spec["tick"], zero=True))
        close, high, low = [Decimal(_decimal(v, tick=spec["tick"])) for v in values[2:5]]
        if not low <= close <= high or opening and not low <= opening <= high:
            _fail("MINUTE_OHLC_INVALID")
        if not opening:
            if index == len(rows) - 1 and stamp.time().replace(tzinfo=None) != time(9, 30):
                _fail("LATEST_MINUTE_OPEN_MISSING_REQUIRES_REVIEW")
            missing_opens += 1
        volume = _volume(values[5])
        _decimal(values[6], zero=True)
        _decimal(values[7], zero=True)
        last = stamp, format(close, "f"), volume
    previous = _decimal(data.get("prePrice"), tick=spec["tick"])
    if abs(Decimal(last[1]) / Decimal(previous) - 1) > Decimal("0.20"):
        _fail("PRICE_SCALE_OR_LARGE_CHANGE_REQUIRES_REVIEW")
    return {"symbol": symbol, "name": data["name"], "source": "eastmoney-minute", "sourceUrl": MINUTE_URL,
        "price": last[1], "previousClose": previous, "currency": "CNY", "priceUnit": "CNY-per-share",
        "tick": spec["tick"], "adjustment": "none", "sourceTimestamp": last[0].isoformat(),
        "timestampKind": "minute-label-not-certified-last-trade", "confirmedLastTradeAt": None,
        "volume": last[2], "volumeUnit": "shares", "independentFeedVerified": False,
        "rowCount": len(rows), "sourceDates": sorted({r.split(" ")[0] for r in rows}),
        "warnings": ["LAST_TRADE_TIME_NOT_PROVIDED", "PUBLIC_WEB_ENDPOINT_NO_SCHEMA_OR_LATENCY_GUARANTEE"] +
            (["ZERO_MINUTE_OPEN_IS_MISSING_NOT_ZERO_PRICE"] if missing_opens else [])}


def _attempt(source, symbol, url, parser, *, deadline=None):
    proxies = getproxies()
    started = datetime.now(timezone.utc)
    result = {"source": source, "symbol": symbol, "endpoint": url.split("?")[0],
        "requestedAt": started.isoformat(), "receivedAt": None, "httpStatus": None,
        "elapsedMs": None, "rawSha256": None, "rawBytes": None, "errors": [],
        "headers": {}, "transport": {"osHttpsProxyConfigured": bool(proxies.get("https")),
            "osBypassForHost": bool(proxy_bypass(urlsplit(url).hostname)), "tlsVerification": True,
            "proxyConfigurationModified": False, "attempts": 1}, "value": None}
    raw = None
    try:
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds() if deadline is not None else TIMEOUT
        if remaining <= 0:
            _fail("REPORT_DEADLINE_EXCEEDED_NO_REQUEST")
        with default_opener().open(Request(url, headers={"User-Agent": "GooseResearch/Investment (personal research)",
                "Accept": "*/*"}), timeout=min(TIMEOUT, remaining)) as response:
            result["httpStatus"] = response.status
            if response.status != 200 or response.geturl() != url:
                _fail("HTTP_OR_REDIRECT_INVALID")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raw = None
                _fail("UPSTREAM_BODY_TOO_LARGE")
            result.update(rawBytes=len(raw), rawSha256=sha256(raw).hexdigest(),
                headers={key: (response.headers.get(key) or "")[:150] for key in ("Date", "Age", "Content-Type")})
            if deadline is not None and datetime.now(timezone.utc) >= deadline:
                _fail("REPORT_DEADLINE_EXCEEDED_RESPONSE_REJECTED")
            result["value"] = parser(raw)
    except ValueError as exc:
        code = str(exc)
        result["errors"] = [code if re.fullmatch("[A-Z0-9_]{1,100}", code) else "UPSTREAM_FIELDS_INVALID"]
    except (URLError, OSError, http.client.HTTPException) as exc:
        result["errors"] = [error_code(exc)]
        if isinstance(exc, HTTPError):
            result["httpStatus"] = exc.code
    finished = datetime.now(timezone.utc)
    result.update(receivedAt=finished.isoformat(), elapsedMs=round((finished - started).total_seconds() * 1000))
    return result, raw


def classify_quote(quote, observation, now):
    """Postclose prices are dated snapshots, never today's intraday authorization."""
    quote = dict(quote)
    local = now.astimezone(BJ)
    stamp = datetime.fromisoformat(quote["sourceTimestamp"]).astimezone(BJ)
    warnings = list(quote.get("warnings", []))
    codes = []
    try:
        session = local.date().isoformat() in xshg_sessions(local.date().isoformat(), local.date().isoformat())
        source_session = stamp.date().isoformat() in xshg_sessions(stamp.date().isoformat(), stamp.date().isoformat())
    except ValueError:
        session, source_session = False, False
        codes.append("CALENDAR_UNVERIFIED_STOP")
    receipt = datetime.fromisoformat(observation["receivedAt"])
    request = datetime.fromisoformat(observation["requestedAt"])
    today = stamp.date() == local.date()
    now_time = local.time().replace(tzinfo=None)
    continuous = session and (time(9, 30) <= now_time <= time(11, 30) or time(13) <= now_time < time(14, 57))
    age = (now - stamp).total_seconds()
    if stamp > receipt or receipt < request or (now - receipt).total_seconds() < -2:
        codes.append("FUTURE_OR_REVERSED_CLOCK")
    if not source_session:
        codes.append("SOURCE_DATE_NOT_VERIFIED_SESSION")
    if quote.get("volume", 0) <= 0:
        codes.append("NO_POSITIVE_VOLUME")
    if observation["elapsedMs"] > TIMEOUT * 1000:
        codes.append("REQUEST_LATENCY_EXCEEDED")
    if (now - receipt).total_seconds() > 30:
        codes.append("RECEIPT_TOO_OLD_RECHECK_REQUIRED")
    headers = observation.get("headers", {})
    if headers.get("Age") and (not re.fullmatch(r"\d+", headers["Age"]) or int(headers["Age"]) > 30):
        codes.append("HTTP_CACHE_AGE_INVALID_OR_EXCESSIVE")
    if headers.get("Date"):
        try:
            server_time = parsedate_to_datetime(headers["Date"])
            if server_time.tzinfo is None or abs((server_time - receipt).total_seconds()) > 60:
                codes.append("HTTP_SERVER_CLOCK_SKEW")
        except (TypeError, ValueError, OverflowError):
            codes.append("HTTP_SERVER_DATE_INVALID")
    if today and time(15) <= now_time and stamp.time().replace(tzinfo=None) >= time(15):
        status = "closing-snapshot"
        warnings.append("POSTCLOSE_DATED_SNAPSHOT_NOT_INTRADAY_SIGNAL")
    elif continuous and today and 0 <= age <= 90:
        status = "intraday-verified"
    else:
        status = "historical-snapshot"
        warnings.append("INTRADAY_NOT_VERIFIED_NO_TODAY_TRADE_SIGNAL")
    if continuous and (not today or age > 90):
        codes.append("INTRADAY_QUOTE_STALE_OR_NOT_TODAY")
    if codes:
        status = "blocked"
    quote.update(fetchedAt=observation["receivedAt"], rawSha256=observation["rawSha256"],
        quality={"status": status, "today": today, "unitVerified": True,
            "intradaySignalAllowed": status == "intraday-verified", "independentFeedVerified": False,
            "codes": codes, "warnings": warnings, "ageSeconds": round(age, 3)})
    return quote


def _daily_parser(raw, symbol, start, end, adjustment="none"):
    # Tencent's wrapper is data, never JavaScript evaluation.
    text = raw.decode("utf-8-sig").strip()
    if text.startswith("goose="):
        text = text[6:].rstrip(";")
    doc = strict_json(text.encode("utf-8"))
    provider = symbol[-2:].lower() + symbol[:6]
    if type(doc) is not dict or type(doc.get("code")) is not int or doc["code"] != 0 or type(doc.get("data")) is not dict:
        _fail("TENCENT_DAILY_RESPONSE_INVALID")
    data = doc["data"].get(provider)
    key = "day" if adjustment == "none" else "qfqday"
    if type(data) is not dict or type(data.get(key)) is not list:
        _fail("TENCENT_DAILY_BASIS_OR_SYMBOL_INVALID")
    selected = []
    for row in data[key]:
        if type(row) is not list or len(row) < 6 or not all(type(value) is str for value in row[:5]):
            _fail("TENCENT_DAILY_ROW_INVALID")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[0]):
            _fail("TENCENT_DAILY_DATE_INVALID")
        if start <= row[0] <= end:
            prices = [_decimal(value) for value in row[1:5]]
            opening, close, high, low = map(Decimal, prices)
            if not low <= min(opening, close) <= max(opening, close) <= high:
                _fail("DAILY_OHLC_RANGE_INVALID")
            selected.append({"date": row[0], "open": prices[0], "close": prices[1],
                "high": prices[2], "low": prices[3], "volume": _volume(row[5]), "publishedAt": None})
    selected.sort(key=lambda row: row["date"])
    if [row["date"] for row in selected] != xshg_sessions(start, end):
        _fail("DAILY_CALENDAR_GAP_DUPLICATE_OR_NON_SESSION")
    return selected


def _dataset(symbol, bars, start, end, observation, quote):
    spec = SPECS[symbol]
    sessions = xshg_sessions(start, end)
    unit_ok = symbol == "600900.SH"
    evidence = "AKShare/Tencent reviewed unadjusted CNY field; no independent exchange-feed certification"
    if symbol == "515450.SH" and quote and quote["quality"]["status"] != "blocked":
        unit_ok = all(Decimal(row[key]) % Decimal(spec["tick"]) == 0 for row in bars for key in ("open", "high", "low", "close"))
        # The same-date source comparison is a scale/schema consistency check,
        # not two independent feeds and not a corporate-action reconciliation.
        if bars and bars[-1]["date"] == quote["sourceTimestamp"][:10]:
            unit_ok = unit_ok and abs(Decimal(bars[-1]["close"]) - Decimal(quote["price"])) <= Decimal(spec["tick"])
        evidence = UNIT_REFERENCE + " ; Tencent unadjusted decimal CNY/share, ETF 0.001 tick and quote/daily scale consistency; not independent feed verification"
    doc = {"format": "goose-market-dataset", "version": 1, "symbol": symbol, "kind": spec["kind"],
        "source": "tencent", "sourceUrl": TENCENT_URL, "adjustment": "none", "currency": "CNY",
        "priceUnit": "CNY-per-share", "fetchedAt": observation["receivedAt"],
        "calendar": {"name": "XSHG", "sessions": sessions, "supportedThrough": CALENDAR_SUPPORTED_THROUGH},
        "bars": bars, "quality": {"status": "validated" if unit_ok else "blocked", "crossValidated": False,
            "unitVerified": unit_ok, "unitEvidence": evidence, "corporateActionsVerified": False,
            "warnings": ["PRICE_RETURN_EXCLUDES_CASH_DIVIDENDS_NOT_TOTAL_INVESTMENT_RETURN"]}}
    if symbol == "515450.SH" and etf_anomaly_diagnostics(doc)["status"] == "blocked":
        doc["quality"]["status"] = "blocked"
    return validate_dataset(doc)


def collect_market(symbols, output_dir, *, now=None, history_days=320, deadline=None):
    """Collect live public responses; output_dir MUST be a local ignored work dir.

    Known instruments are reviewed explicitly. Unknown user instruments return
    a diagnostic until identity/type/tick mapping has been reviewed.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or not 30 <= history_days <= 600:
        _fail("AWARE_CLOCK_AND_BOUNDED_HISTORY_REQUIRED")
    if deadline is not None and (not isinstance(deadline, datetime) or deadline.tzinfo is None):
        _fail("AWARE_DEADLINE_REQUIRED")
    day = now.astimezone(BJ).date().isoformat()
    # Calendar range failure stops BEFORE any network or output mutation.
    xshg_sessions(day, day)
    symbols = list(dict.fromkeys(symbols))
    if len(symbols) > 20:
        _fail("SYMBOL_LIMIT_EXCEEDED")
    # Inspect the original absolute spelling before resolve() can hide a link.
    target = Path(os.path.abspath(output_dir))
    if ".." in Path(output_dir).parts:
        _fail("MARKET_OUTPUT_PARENT_TRAVERSAL_FORBIDDEN")
    for parent in (target, *target.parents):
        try:
            stat = parent.lstat()
        except FileNotFoundError:
            continue
        if parent.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
            _fail("MARKET_OUTPUT_REPARSE_POINT_FORBIDDEN")
    resolved = target.resolve()
    if not resolved.is_relative_to(ROOT) or "work" not in resolved.relative_to(ROOT).parts:
        _fail("MARKET_OUTPUT_REQUIRES_PROJECT_IGNORED_WORK_DIRECTORY")
    target.mkdir(parents=True, exist_ok=True)
    result = {"format": "goose-investment-market", "version": 1, "generatedAt": now.isoformat(),
        "context": {"localDate": now.astimezone(BJ).date().isoformat(), "independentFeedsVerified": False,
            "corporateActionsVerified": False, "totalReturnAvailable": False},
        "quotes": {}, "histories": {}, "adjustedHistories": {}, "diagnostics": []}
    requests = []
    for symbol in symbols:
        result["quotes"][symbol], result["histories"][symbol] = None, None
        if symbol not in SPECS:
            result["diagnostics"].append({"symbol": symbol, "errors": ["INSTRUMENT_NOT_REVIEWED_IDENTITY_AND_UNIT_REQUIRED"]})
            continue
        requests.append(("tencent-quote", symbol, QUOTE_URL + symbol[-2:].lower() + symbol[:6], lambda raw, s=symbol: parse_tencent(raw, s)))
        requests.append(("eastmoney-minute", symbol, MINUTE_URL + "?" + urlencode({
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ndays": "1", "iscr": "0", "secid": "1." + symbol[:6]}), lambda raw, s=symbol: parse_eastmoney(raw, s)))
    def save_attempt(args):
        observation, raw = _attempt(*args, deadline=deadline)
        if raw is not None:
            name = args[1] + "-" + args[0] + ".raw"
            (target / name).write_bytes(raw)
            observation["rawFile"] = name
        return observation
    with ThreadPoolExecutor(max_workers=3) as pool:
        observations = list(pool.map(save_attempt, requests))
    # A quote's age is measured against actual receipt, not collection start.
    checked_now = datetime.now(timezone.utc) if abs((datetime.now(timezone.utc) - now).total_seconds()) < 120 else now
    candidates = {}
    for observation in observations:
        value = observation.pop("value")
        if value:
            value = classify_quote(value, observation, checked_now)
            observation["quality"] = value["quality"]
            candidates.setdefault(observation["symbol"], []).append(value)
        result["diagnostics"].append(observation)
    for symbol, values in candidates.items():
        good = [q for q in values if q["quality"]["status"] != "blocked"]
        selected = (good or values)[0]
        if selected["source"] == "eastmoney-minute":
            selected["quality"]["warnings"].append("EXPLICIT_FALLBACK_TENCENT_UNAVAILABLE_OR_INVALID")
        if len(good) == 2:
            a, b = good
            # Postclose compare same-day snapshots; intraday sources must also
            # be close enough in time to justify a numerical comparison.
            astamp, bstamp = map(datetime.fromisoformat, (a["sourceTimestamp"], b["sourceTimestamp"]))
            comparable = astamp.date() == bstamp.date() and (a["quality"]["status"] == b["quality"]["status"] == "closing-snapshot" or abs((astamp - bstamp).total_seconds()) <= 60)
            if comparable:
                bps = abs(Decimal(a["price"]) / Decimal(b["price"]) - 1) * 10000
                selected["quality"]["publicEndpointPriceComparison"] = {"differenceBps": format(bps, "f"), "independentFeedsVerified": False}
                if bps > 10:
                    selected["quality"]["status"] = "blocked"
                    selected["quality"]["intradaySignalAllowed"] = False
                    selected["quality"]["codes"].append("PUBLIC_ENDPOINT_PRICES_DISAGREE_OVER_10_BPS")
        result["quotes"][symbol] = selected
    local = now.astimezone(BJ)
    end_date = local.date() if local.time().replace(tzinfo=None) >= time(15, 30) else local.date() - timedelta(days=1)
    end = end_date.isoformat()
    start = (end_date - timedelta(days=history_days)).isoformat()
    for symbol in symbols:
        if symbol not in SPECS:
            continue
        provider = symbol[-2:].lower() + symbol[:6]
        for adjustment in ("none", "qfq"):
            # Separate named outputs: qfq values are never mixed into raw bars.
            query = {"_var": "goose", "param": f"{provider},day,{start},{end},640,{'' if adjustment == 'none' else 'qfq'}"}
            observation = save_attempt(("tencent-daily-" + adjustment, symbol, TENCENT_URL + "?" + urlencode(query),
                lambda raw, s=symbol, a=adjustment: _daily_parser(raw, s, start, end, a)))
            bars = observation.pop("value")
            if bars is not None:
                try:
                    if adjustment == "none":
                        result["histories"][symbol] = _dataset(symbol, bars, start, end, observation, result["quotes"][symbol])
                    else:
                        # Basis-only diagnostic, not a dividend-cash ledger and
                        # not a total-return backtest with event/withholding tax.
                        result["adjustedHistories"][symbol] = {"symbol": symbol, "adjustment": "qfq",
                            "source": "tencent", "sourceUrl": TENCENT_URL, "fetchedAt": observation["receivedAt"],
                            "bars": bars, "usableForExecutablePriceBacktest": False, "totalReturnVerified": False,
                            "quality": {"status": "validated-basis-only", "ohlcAndCalendarValidated": True,
                                "independentFeedVerified": False, "corporateActionsVerified": False},
                            "warnings": ["PROVIDER_QFQ_DIAGNOSTIC_NOT_VERIFIED_DIVIDEND_TOTAL_RETURN", "DO_NOT_MIX_WITH_UNADJUSTED_EXECUTION_PRICES"]}
                    observation["barCount"] = len(bars)
                    observation["lastDate"] = bars[-1]["date"] if bars else None
                except ValueError as exc:
                    observation["errors"].append(str(exc))
            result["diagnostics"].append(observation)
    (target / "market.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
