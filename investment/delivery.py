"""An explicit, once-per-Beijing-day Bark transport. Never run implicitly.

No portfolio, credentials, private URL, exception text or response body is put
in the outgoing summary or the local delivery log. A request with an ambiguous
outcome consumes the day's claim, so an interrupted job cannot resend it.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import uuid
from urllib.error import HTTPError
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
BJ = timezone(timedelta(hours=8))
for relative in ("v32", "v33"):
    if str(ROOT / relative) not in sys.path:
        sys.path.insert(0, str(ROOT / relative))
from goose_research.notify import _configuration, _default_opener, _strict_json

HEALTH_CODES = frozenset({
    'ACCOUNT_CHANGED_DURING_ANALYSIS',
    "INTRADAY_VERIFIED", "MARKET_INVALID_OR_STALE", "ANALYSIS_FAILED",
    "DEADLINE_EXCEEDED", "CALENDAR_UNVERIFIED", "NOT_A_TRADING_DAY",
    "OUTSIDE_REPORT_WINDOW", "ALREADY_CLAIMED", "REPORT_UNTRUSTED",
})
HOLDINGS = {"current-user-confirmed": "当日用户快照",
    "stale-snapshot": "旧持仓仅作重估，不是当前账户核验",
    "partial-accounts": "账户不完整", "missing": "持仓缺失"}


def _clock(now=None):
    value = now() if callable(now) else now or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("AWARE_CLOCK_REQUIRED")
    return value.astimezone(BJ)


def safe_directory(root, relative):
    target = Path(os.path.abspath(root or ROOT)) / "investment" / "work" / relative
    for parent in (target, *target.parents):
        if parent.exists() and (parent.is_symlink() or parent.is_junction()):
            raise ValueError("OUTPUT_REPARSE_REJECTED")
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_once(path, value):
    with Path(path).open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _public_body(event, now):
    if not isinstance(event, dict) or event.get("format") != "goose-investment-daily" or type(event.get("version")) is not int or event["version"] != 1:
        raise ValueError("EVENT_REJECTED")
    if event.get("origin") != "local-daily-pipeline" or event.get("validationMode") != "live":
        raise ValueError("EVENT_REJECTED")
    if event.get("synthetic") is not False or event.get("code") not in HEALTH_CODES:
        raise ValueError("EVENT_REJECTED")
    day, created = event.get("day"), event.get("completedAt")
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("EVENT_REJECTED")
    from goose_research.market import xshg_sessions
    if day not in xshg_sessions(day, day):
        raise ValueError("EVENT_REJECTED")
    completed = datetime.fromisoformat(created)
    if completed.tzinfo is None or completed.astimezone(BJ).date().isoformat() != day or now.date().isoformat() != day:
        raise ValueError("EVENT_REJECTED")
    deadline = datetime.combine(now.date(), time(14, 44), BJ)
    if completed > now or now >= deadline or completed >= deadline or (now-completed).total_seconds() > 90:
        raise ValueError("EVENT_STALE_OR_LATE")
    if not time(14, 38) <= completed.astimezone(BJ).time().replace(tzinfo=None) < time(14, 44):
        raise ValueError("EVENT_REJECTED")
    status = event.get("holdingsStatus")
    if status not in HOLDINGS:
        raise ValueError("EVENT_REJECTED")
    # Reject an untrusted/synthetic generator even if only fixed fields would
    # otherwise survive serialization. No input free text is ever copied.
    if event.get("reportTrusted") is not True:
        raise ValueError("EVENT_REJECTED")
    code = event["code"]
    health = "当天盘中报价核验通过" if code == "INTRADAY_VERIFIED" else "本次数据或流程不合格，不沿用旧信号"
    radar=''
    if 'radarStatus' in event:
        counts=[event.get('radarETFCount'),event.get('radarStockCount')]
        if any(type(n) is not int or not 0<=n<=5 for n in counts):raise ValueError('RADAR_COUNTS_INVALID')
        radar=(f"机会雷达：ETF观察{counts[0]}项、股票观察{counts[1]}项。" if code=='INTRADAY_VERIFIED' and event['radarStatus'] in {'ready','partial'} else '本次雷达未完成或行情不合格，未沿用旧候选。')
    return f"{day} 养鹅投资研究：{health}；{HOLDINGS[status]}；诊断 {code}。观察，不执行交易。{radar}请在养鹅私有入口查看完整报告。"


def send_daily(event, root=None, config_path=None, *, confirmed=False, opener=None, now=None):
    """One external request at most. Only explicit future approval enables it.

    A service acknowledgement is not proof that an iPhone received anything.
    Config discovery and V1 Bark configuration reads are deliberately absent.
    """
    base = {"sent": False, "phoneArrivalVerified": False}
    if confirmed is not True:
        return {**base, "status": "blocked", "code": "EXPLICIT_SEND_APPROVAL_REQUIRED"}
    try:
        clock = _clock(now)
        body = _public_body(event, clock)
    except Exception:
        return {**base, "status": "blocked", "code": "EVENT_UNTRUSTED_STALE_OR_LATE"}
    if config_path is None:
        return {**base, "status": "blocked", "code": "EXPLICIT_CONFIG_PATH_REQUIRED"}
    try:
        target, key, archive = _configuration(config_path)
    except Exception:
        return {**base, "status": "blocked", "code": "CONFIG_REJECTED"}
    folder = safe_directory(root, "daily-delivery")
    claim = folder / (event["day"] + ".claim.json")
    try:
        # Exclusive creation is atomic across processes; no report-ID loophole.
        write_once(claim, {"day": event["day"], "claimedAt": clock.isoformat(),
            "status": "request-may-have-started-no-automatic-resend"})
    except FileExistsError:
        return {**base, "status": "blocked", "code": "DAY_ALREADY_CLAIMED_NO_RESEND"}
    payload = {"device_key": key, "title": "养鹅投资研究", "body": body,
        "group": "GooseInvestment", "isArchive": int(archive)}
    request = Request(target,
        data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf8"),
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"}, method="POST")
    result = {**base, "status": "unknown", "code": "DELIVERY_OUTCOME_UNKNOWN_NO_RESEND"}
    try:
        # Recheck after potentially slow configuration/disk work and before I/O.
        transport = opener if opener is not None else _default_opener()
        opening = transport.open if hasattr(transport, "open") else transport
        remaining = (datetime.combine(clock.date(), time(14, 44), BJ) - _clock(now)).total_seconds()
        if remaining <= 0:
            result = {**base, "status": "blocked", "code": "DEADLINE_PASSED_BEFORE_REQUEST"}
        else:
            with opening(request, timeout=min(8, remaining)) as response:
                status = response.status if hasattr(response, "status") else response.getcode()
                raw = response.read(65537)
                if type(status) is not int or status != 200 or not isinstance(raw, bytes) or len(raw) > 65536:
                    result = {**base, "status": "rejected", "code": "SERVICE_RESPONSE_REJECTED_NO_RESEND"}
                else:
                    decoded = _strict_json(raw.decode("utf8"))
                    if not isinstance(decoded, dict) or type(decoded.get("code")) is not int or decoded["code"] != 200:
                        result = {**base, "status": "rejected", "code": "SERVICE_RESPONSE_REJECTED_NO_RESEND"}
                    else:
                        late = _clock(now) >= datetime.combine(clock.date(), time(14, 44), BJ)
                        result = {**base, "sent": True, "status": "service-accepted",
                            "code": "SERVICE_ACK_AFTER_DEADLINE" if late else "SERVICE_ACK_NOT_PHONE_ARRIVAL"}
    except HTTPError:
        result = {**base, "status": "rejected", "code": "SERVICE_HTTP_REJECTED_NO_RESEND"}
    except Exception:
        pass  # Timeout/crash might have delivered; never automatically replay.
    write_once(folder / (event["day"] + ".result-" + uuid.uuid4().hex + ".json"), result)
    return result
