"""Explicit one-shot Bark test delivery; never scheduled or called implicitly.

Only a human-confirmed future CLI action may invoke the transport. Tests inject
an opener and use fictional configuration files. No V1 environment/configuration
is discovered, no message data is logged, and no device-arrival claim is made.
"""
from __future__ import annotations

from datetime import date
import ipaddress
import json
from pathlib import Path
import re
import ssl
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

TITLE = "养鹅计划V3.2/单次测试"
GROUP = "GooseResearch-Test"
HEALTH_CODES = frozenset({
    "TEST_ONLY_NO_BARK_SENT", "NO_INTRADAY_QUOTE", "NO_TODAY_EXECUTABLE_SIGNAL",
    "AFTER_1445_NOT_A_PRE_CUTOFF_REPORT", "OUTSIDE_MARKET_OBSERVATION_WINDOW",
    "NOT_AN_XSHG_SESSION", "CURRENT_CALENDAR_NOT_VERIFIED", "SYNTHETIC_NOT_MARKET_DATA",
    "DATASET_BLOCKED",
})


def _failed(code):
    return {"status": "failed", "code": code, "phoneArrivalVerified": False}


def _strict_json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result or key in {"__proto__", "constructor", "prototype"}:
                raise ValueError("INVALID_JSON")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("INVALID_JSON")))


def _summary(health):
    if not isinstance(health, dict) or health.get("format") != "goose-pre1445-test" or type(health.get("version")) is not int or health["version"] != 1:
        raise ValueError("INVALID_HEALTH")
    notification = health.get("notification")
    observation = health.get("strategyObservation")
    if not isinstance(notification, dict) or notification.get("mode") != "dry-run" or notification.get("sent") is not False:
        raise ValueError("INVALID_HEALTH")
    if notification.get("phoneArrivalVerified", False) is not False:
        raise ValueError("INVALID_HEALTH")
    if not isinstance(observation, dict) or observation.get("stance") != "watch" or observation.get("decisionAllowed") is not False:
        raise ValueError("INVALID_HEALTH")
    data = health.get("dataHealth")
    if not isinstance(data, dict):
        raise ValueError("INVALID_HEALTH")
    # Closed fields are essential: never serialize an input report, preview,
    # exception, account or arbitrary diagnostic into the outgoing message.
    symbol, through, codes = data.get("symbol"), data.get("through"), health.get("codes")
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(SH|SZ)", symbol):
        raise ValueError("INVALID_HEALTH")
    if not isinstance(through, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", through) or date.fromisoformat(through).isoformat() != through:
        raise ValueError("INVALID_HEALTH")
    if not isinstance(codes, list) or not codes or len(codes) > len(HEALTH_CODES) or any(not isinstance(code, str) or code not in HEALTH_CODES for code in codes) or len(set(codes)) != len(codes):
        raise ValueError("INVALID_HEALTH")
    return f"单次人工流程测试；标的{symbol}；数据截至{through}；诊断{' / '.join(codes)}。观察，不执行交易。"


def _configuration(path):
    # This is the only configuration source; no environment fallback, V1
    # loader, directory discovery, key-in-URL or payload override is allowed.
    with Path(path).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("INVALID_CONFIG")
    value = _strict_json(raw.decode("utf-8-sig"))
    if not isinstance(value, dict) or set(value)-{"server", "device_key", "is_archive"}:
        raise ValueError("INVALID_CONFIG")
    server, key, archive = value.get("server"), value.get("device_key"), value.get("is_archive", True)
    if not isinstance(server, str) or len(server) > 2048 or re.search(r"[\s\x00-\x1f\x7f]", server) or "?" in server or "#" in server:
        raise ValueError("INVALID_CONFIG")
    parsed = urlsplit(server)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"} or parsed.netloc.endswith(":"):
        raise ValueError("INVALID_CONFIG")
    host = parsed.hostname
    if len(host) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in host.split(".")):
        raise ValueError("INVALID_CONFIG")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("INVALID_CONFIG")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("INVALID_CONFIG")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", key) or type(archive) is not bool:
        raise ValueError("INVALID_CONFIG")
    return server.rstrip("/")+"/push", key, archive


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener():
    # build_opener retains urllib's system proxy handler. The default SSL
    # context keeps certificate and hostname validation enabled.
    return build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))


def send_manual_test(pre1445_health, config_path, *, confirmed=False, opener=None):
    """One request at most, after explicit confirmation. Never verifies arrival.

    Failure dictionaries contain fixed diagnostics only. Response bodies,
    secrets, configuration paths, hostnames and raw exceptions are not returned.
    This function does not mutate the health report's dry-run notification state.
    """
    if confirmed is not True:
        return _failed("MANUAL_TEST_CONFIRMATION_REQUIRED")
    try:
        summary = _summary(pre1445_health)
    except Exception:
        return _failed("MANUAL_TEST_HEALTH_REJECTED")
    try:
        target, key, archive = _configuration(config_path)
    except Exception:
        return _failed("MANUAL_TEST_CONFIG_REJECTED")
    payload = {"device_key": key, "title": TITLE, "body": summary,
        "group": GROUP, "isArchive": int(archive)}
    request = Request(target, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"}, method="POST")
    try:
        transport = opener if opener is not None else _default_opener()
        opening = transport.open if hasattr(transport, "open") else transport
        with opening(request, timeout=8) as response:
            status = response.status if hasattr(response, "status") else response.getcode()
            if type(status) is not int or status != 200:
                return _failed("MANUAL_TEST_HTTP_REJECTED")
            raw = response.read(65537)
            if not isinstance(raw, bytes) or len(raw) > 65536:
                return _failed("MANUAL_TEST_RESPONSE_REJECTED")
            result = _strict_json(raw.decode("utf-8"))
            if not isinstance(result, dict) or type(result.get("code")) is not int or result["code"] != 200:
                return _failed("MANUAL_TEST_RESPONSE_REJECTED")
    except HTTPError:
        return _failed("MANUAL_TEST_HTTP_REJECTED")
    except Exception:
        return _failed("MANUAL_TEST_TRANSPORT_FAILED")
    return {"status": "service-accepted", "phoneArrivalVerified": False}
