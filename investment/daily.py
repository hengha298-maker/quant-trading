"""Pre-14:45 orchestration, defaulting to a local notification preview.

No task is registered here. Wall-clock boundaries use Asia/Shanghai's UTC+8.
No missed run, failed request or interrupted date claim is replayed implicitly.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
import json
from pathlib import Path
import re
import uuid

from .delivery import BJ, ROOT, _clock, safe_directory, send_daily, write_once

START = time(14, 38)
ANALYSIS_CUTOFF = time(14, 43, 30)
DEADLINE = time(14, 44)
KNOWN_SOURCES = {"tencent-quote", "eastmoney-minute"}


def _sessions(day):
    from goose_research.market import xshg_sessions
    return xshg_sessions(day, day)


def schedule_plan(*, now=None):
    from goose_research.market import calendar_supported_through
    clock = _clock(now)
    return {"scheduledAt": "14:38:00+08:00", "reportDeadline": "14:44:00+08:00",
        "analysisDeadline": "14:43:30+08:00",
        "targetArrival": "14:45:00+08:00 (delivery is not guaranteed)",
        "taskEnabled": False, "barkEnabled": False, "old1450TaskModified": False,
        "calendarSupportedThrough": calendar_supported_through(), "localDate": clock.date().isoformat(),
        "missedRunReplay": False, "automaticRetries": False,
        "computerWakeEnabled": False, "requiresComputerOnline": True}


def _report_health(report, clock):
    """No old report, post-close snapshot or unreviewed quote can pass."""
    if not isinstance(report, dict) or report.get("format") != "goose-live-research" or type(report.get("version")) is not int or report["version"] != 1:
        return "REPORT_UNTRUSTED", False, "missing"
    if report.get("synthetic") or report.get("testData") or report.get("cachedReport"):
        return "REPORT_UNTRUSTED", False, "missing"
    try:
        generated = datetime.fromisoformat(report["generatedAt"])
        if generated.tzinfo is None or generated.astimezone(BJ).date() != clock.date() or generated > clock:
            return "REPORT_UNTRUSTED", False, "missing"
        if not START <= generated.astimezone(BJ).time().replace(tzinfo=None) < DEADLINE or (clock-generated).total_seconds() > 360:
            return "REPORT_UNTRUSTED", False, "missing"
    except (ValueError, KeyError, TypeError):
        return "REPORT_UNTRUSTED", False, "missing"
    pf = report.get("portfolio", {})
    holdings = pf.get("status", "missing")
    if holdings not in {"missing", "current-user-confirmed", "stale-snapshot", "partial-accounts"}:
        holdings = "missing"
    if report.get("deadlineMissed") is True:
        return "DEADLINE_EXCEEDED", True, holdings
    if report.get('sourceChanged') is True:
        return 'ACCOUNT_CHANGED_DURING_ANALYSIS',True,holdings
    instruments = report.get("instruments", [])
    if not isinstance(instruments, list) or not instruments:
        return "MARKET_INVALID_OR_STALE", True, holdings
    observed = set()
    for item in instruments:
        try:
            symbol, quote = item["symbol"], item["quote"]
            stamp = datetime.fromisoformat(quote["sourceTimestamp"])
            quality = quote["quality"]
            valid = (re.fullmatch(r"\d{6}\.(SH|SZ)", symbol) and quote.get("symbol") == symbol
                and symbol not in observed and quote.get("source") in KNOWN_SOURCES
                and quote.get("adjustment") == "none" and quote.get("currency") == "CNY"
                and quote.get("priceUnit") == "CNY-per-share"
                and quality.get("status") == "intraday-verified" and quality.get("unitVerified") is True
                and quality.get("intradaySignalAllowed") is True and not quote.get("synthetic")
                and stamp.tzinfo is not None and stamp.astimezone(BJ).date() == clock.date()
                and 0 <= (clock-stamp).total_seconds() <= 90)
            if not valid:
                return "MARKET_INVALID_OR_STALE", True, holdings
            observed.add(symbol)
        except (KeyError, TypeError, ValueError):
            return "MARKET_INVALID_OR_STALE", True, holdings
    required = {"600900.SH", "515450.SH"}
    required.update(p.get("symbol") for p in pf.get("positions", []) if isinstance(p, dict))
    if not required.issubset(observed):
        return "MARKET_INVALID_OR_STALE", True, holdings
    return "INTRADAY_VERIFIED", True, holdings


def _save(root, event):
    folder = safe_directory(root, "daily/" + event["day"])
    write_once(folder / (event["attemptId"] + ".json"), event)
    return event


def run_daily(root=None, now=None, generate=None, confirmed_send=False, config_path=None, planning=False):
    """Run once within the verified session/window; never sends by default.

    `now` and `generate` are dependency injection for offline tests, not CLI
    clock overrides. Injected generators cannot cause a real Bark delivery.
    """
    clock = _clock(now)
    day = clock.date().isoformat()
    event = {"format": "goose-investment-daily", "version": 1,
        "attemptId": uuid.uuid4().hex, "origin": "local-daily-pipeline",
        "validationMode": "planning" if planning else "injected-test" if generate is not None or now is not None else "live",
        "synthetic": bool(generate is not None or now is not None), "day": day,
        "startedAt": clock.isoformat(), "completedAt": clock.isoformat(),
        "deadline": datetime.combine(clock.date(), DEADLINE, BJ).isoformat(),
        "analysisDeadline": datetime.combine(clock.date(), ANALYSIS_CUTOFF, BJ).isoformat(),
        "status": "blocked", "code": "OUTSIDE_REPORT_WINDOW", "reportId": None,
        "reportTrusted": False, "marketHealth": "not-checked", "holdingsStatus": "missing",
        "notification": {"mode": "dry-run", "sent": False, "phoneArrivalVerified": False},
        "schedule": schedule_plan(now=clock)}
    try:
        from .operations import pause_state
        if pause_state(root or ROOT).get('paused'):
            event.update(status='paused',code='LOCAL_ANALYSIS_PAUSED')
            return _save(root,event)
    except (ValueError,OSError):
        event.update(status='blocked',code='LOCAL_CONTROL_INVALID')
        return _save(root,event)
    try:
        sessions = _sessions(day)
    except (ValueError, ImportError):
        event["code"] = "CALENDAR_UNVERIFIED"
        return _save(root, event)
    if day not in sessions:
        event["code"] = "NOT_A_TRADING_DAY"
        return _save(root, event)
    if planning or not START <= clock.time().replace(tzinfo=None) < DEADLINE:
        event["status"] = "planning" if planning else "blocked"
        return _save(root, event)
    folder = safe_directory(root, "daily/" + day)
    try:
        write_once(folder / "generation.claim.json", {"day": day, "startedAt": clock.isoformat(),
            "status": "claimed-no-automatic-replay-after-crash"})
    except FileExistsError:
        event["code"] = "ALREADY_CLAIMED"
        return _save(root, event)
    if generate is None:
        from .engine import run_analysis
        generate = run_analysis
    try:
        if _clock(now) >= datetime.combine(clock.date(), ANALYSIS_CUTOFF, BJ):
            event["code"] = "DEADLINE_EXCEEDED"
            event["reportTrusted"] = True
        else:
            report = generate(root, deadline=datetime.combine(clock.date(), ANALYSIS_CUTOFF, BJ))
            radar=report.get('opportunityRadar',{})
            event['radarStatus']=radar.get('status') if radar.get('status') in {'ready','partial','unavailable'} else 'unavailable'
            event['radarETFCount']=min(5,len(radar.get('groups',{}).get('etf',{}).get('candidates',[])))
            event['radarStockCount']=min(5,len(radar.get('groups',{}).get('stock',{}).get('candidates',[])))
            ended = _clock(now)
            event["completedAt"] = ended.isoformat()
            if ended >= datetime.combine(clock.date(), ANALYSIS_CUTOFF, BJ):
                event["code"] = "DEADLINE_EXCEEDED"
                event["reportTrusted"] = True
            else:
                code, trusted, holdings = _report_health(report, ended)
                event.update(code=code, reportTrusted=trusted, holdingsStatus=holdings,
                    reportId=report.get("id") if trusted else None,
                    marketHealth="verified-intraday" if code == "INTRADAY_VERIFIED" else "failed-or-unverified")
    except Exception:
        # Fixed diagnostic only: no response, URL, account or credential text.
        event.update(code="ANALYSIS_FAILED", reportTrusted=True, marketHealth="unavailable")
    event["completedAt"] = _clock(now).isoformat()
    event["status"] = "report-ready" if event["code"] == "INTRADAY_VERIFIED" else "diagnostic"
    event["notification"]["preview"] = "观察，不执行交易。" if event["code"] == "INTRADAY_VERIFIED" else "本次数据或流程不合格，不沿用旧信号。"
    if confirmed_send:
        try:
            if pause_state(root or ROOT).get('paused'):
                event['notification']={'mode':'explicit-send','sent':False,'status':'blocked','code':'LOCAL_ANALYSIS_PAUSED_BEFORE_SEND'}
                return _save(root,event)
        except (ValueError,OSError):
            event['notification']={'mode':'explicit-send','sent':False,'status':'blocked','code':'LOCAL_CONTROL_INVALID'}
            return _save(root,event)
        event["notification"] = {"mode": "explicit-send", **send_daily(event, root,
            config_path, confirmed=True, now=now)}
    return _save(root, event)


def latest_daily(root=None):
    folder = safe_directory(root, "daily")
    files = []
    for day in folder.iterdir():
        if not day.is_dir() or day.is_symlink() or day.is_junction() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name):
            continue
        for path in day.iterdir():
            if re.fullmatch(r"[0-9a-f]{32}\.json", path.name) and path.is_file() and not path.is_symlink() and path.stat().st_size < 32768:
                files.append(path)
    for path in sorted(files, key=lambda p: p.stat().st_mtime_ns, reverse=True):
        try:
            return json.loads(path.read_text(encoding="utf8"))
        except (ValueError, OSError):
            continue
    return None
